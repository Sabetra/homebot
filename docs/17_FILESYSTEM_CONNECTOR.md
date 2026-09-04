<!-- last-verified: 2026-08-25 -->

# 17 — SOTA Filesystem Connector (2026)

## Zweck

Sicherer, deklarativ konfigurierter Dateisystem-Zugriff für den Agenten — inspiriert vom
[Masters of AI Harness](https://github.com/nicepkg/harness) (2026).

**Ziele:**
- Path-Traversal-Schutz via `os.path.realpath()` + Workspace-Boundary-Check
- Symlink-Escape-Block (`os.path.islink()`)
- Binärdatei-Erkennung (null-byte Heuristik)
- Depth-Limiter (Standard: 5) + Char-Limiter (Token-Budget-Schutz)
- Declarative Tool-Availability pro Tab/Mode (wie YAML-Frontmatter)

---

## Architektur

```
┌─────────────────────────────────────────────────────────────┐
│  Orchestrator (orchestrator.py)                              │
│  └─ _is_tool_allowed_for_mode() → filtert Tools pro Tab     │
│  └─ _build_runtime_planner_tool_block() → Tool-Liste        │
├─────────────────────────────────────────────────────────────┤
│  Tool Profiles (agent/tool_profiles.py)                      │
│  └─ TOOL_PROFILES["main_chat"]  → Full FS (R+W)            │
│  └─ TOOL_PROFILES["finance_tab"] → Read-only FS            │
│  └─ TOOL_PROFILES["psych_tab"]   → Kein FS (Privacy!)      │
│  └─ TOOL_PROFILES["settings_tab"] → Read-only Konfig       │
├─────────────────────────────────────────────────────────────┤
│  Tool Schemas (agent/tool_schemas.py)                        │
│  └─ list_directory, search_files (OpenAI-Tool-Format)       │
├─────────────────────────────────────────────────────────────┤
│  Agent Toolkit (agent_toolkit.py)                            │
│  └─ _list_directory() → path_sandbox.list_directory_safe()  │
│  └─ _search_files()   → rg-Content-Suche (P2, Default)      │
│                      Fallback: Name-Suche (Python)          │
├─────────────────────────────────────────────────────────────┤
│  Path Sandbox (agent/path_sandbox.py)                        │
│  └─ resolve()         → realpath + Symlink + Boundary       │
│  └─ read_text()       → Text-Lesen, kein Char-Limit         │
│  └─ write_text()      → Text-Schreiben (wenn erlaubt)       │
│  └─ list_directory_safe() → Directory-Listing + Depth       │
│  └─ search_files_safe()   → Name-Suche (Fallback) + Depth   │
│  └─ search_content_rg()   → rg --json: Caps, Timeout (P2)   │
│  └─ read_file_safe()      → Binary-Check + Char-Limit       │
└─────────────────────────────────────────────────────────────┘
```

---

## Dateisystem-Tools

### `list_directory`

Listet Dateien und Verzeichnisse in einem Pfad auf.

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|-------------|
| `path` | `string` | — | Zielverzeichnis (erforderlich) |
| `max_depth` | `integer` | `5` | Maximale Rekursionstiefe |

**Rückgabe:** Liste von `FileInfo`-Objekten:
```python
@dataclass
class FileInfo:
    name: str       # Dateiname
    path: str       # Vollständiger Pfad
    is_dir: bool    # True = Verzeichnis
    size: int       # Größe in Bytes (0 für Verzeichnisse)
    modified: float # Unix-Timestamp der letzten Änderung
```

### `file_reader`

Liest den Inhalt einer Textdatei (Sandbox-validiert).

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|-------------|
| `file_path` | `string` | — | Datei-Pfad (erforderlich) |
| `offset` | `integer` | `1` | Startzeile (1-basiert); Weiterschreibung über `next_offset` (P1) |
| `limit` | `integer` | `2000` | Anzahl der zu lesenden Zeilen (P1) |
| `encoding` | `string` | `utf-8` | Zeichenkodierung |

**Rückgabe (P1, 2026-08-24):** `success`, `file_path`, `content`, `size`,
`total_lines`, `start_line`, `end_line`, `has_more_lines`, `next_offset`,
`total_chars`, `was_truncated` — bei Trunkierung/Offset über EOF zusätzlich
`truncated_at` und `suggested_action`. `next_offset` ist immer `int`
(Voll-Read → `total_lines + 1`, Offset über EOF → Offset selbst).

**Limits (P0 Context-Safety, 2026-08-24):**

| Limit | Wert | Verhalten bei Überschreitung |
|-------|------|------------------------------|
| Char-Limit | 50.000 Zeichen | Trunkierung + `was_truncated=true` + `suggested_action` |
| Byte-Limit | 20 MB | `PathSandboxError` → `error_class: sandbox_error` (P1: 50 → 20 MB, Token-Budget-Schutz; verankert in `test_oversize_byte_guard_preserved`) |
| Binärdateien | — | `PathSandboxError` (null-byte Heuristik) |

> 2026-08-24: `file_reader` nutzt jetzt `read_file_safe()` (Hard-Char-Limit,
> `errors="replace"`). Davor: `read_text()` ohne Char-Limit → große Dateien
> liefen ungekürzt in den LLM-Kontext (Produktions-Bug, behoben).

### `search_files`

**P2 (2026-08-25): Default-Content-Suche im Dateiinhalt** (ripgrep-Backend,
`rg --json`); reine Dateinamen-Suche läuft mit `content_search=false`.
Inspiriert: Claude Code Grep-Verhalten (2026). Keine neue pip-Abhängigkeit —
rg ist lokal verfügbar; ohne rg greift die Python-Name-Suche als Fallback.

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|-------------|
| `root_path` | `string` | — | Startverzeichnis (erforderlich, sandboxiert) |
| `pattern` | `string` | — | Regex-Pattern (erforderlich) |
| `content_search` | `boolean` | `true` | `true` (Default): Dateiinhalt durchsuchen; `false`: nur Dateinamen |
| `case_insensitive` | `boolean` | `false` | `true`: Groß-/Kleinschreibung ignorieren (Content: `rg -i`; Name: `re.IGNORECASE`) |
| `fixed_string` | `boolean` | `false` | `true`: Pattern als exakter String behandeln (→ `rg -F`; kein Regex-Parser, schneller, keine Injection) |
| `glob` | `string` | — | Glob-Filter, z. B. `*.py` (→ `rg --glob`; nur Content-Suche) |
| `hidden` | `boolean` | `false` | `true`: versteckte Dateien/Verzeichnisse einbeziehen (Default: aus, `.gitignore` wird beachtet) |
| `context` | `integer` | `0` | Kontextzeilen vor/nach Matches, 0–100 (→ `rg -C`; nur Content-Suche) |
| `max_results` | `integer` | `50` | Ergebnis-Cap, Hard-Max 200 (→ `rg --max-count`; Trunkierung bei Cap) |
| `timeout` | `number` | `10.0` | rg-Timeout in Sekunden, Hard-Max 60 (→ Kill; Partial-Hits bleiben erhalten) |

**Rückgabe (P2, 2026-08-25):** `success: true` + `hits` (Liste von
`file`, `line`, `snippet`, `match_type` `content` | `name`), `total`,
`truncated` (Cap erreicht), `partial` + `timed_out` (nur bei rg-Timeout —
bereits gefundene Hits bleiben in `hits` erhalten).

**Rückgabe (Fallback, `content_search=false` oder rg nicht verfügbar):**
`hits` mit `path`, `name`, `match_type: name` (Python-Name-Suche,
`PathSandbox.search_files_safe`).

**Fehler-Vertrag (alle Pfade):** `success: false` + `error_code`
(`sandbox_error` → zusätzlich `needs_user_permission: true` +
`allowed_tools: ["execute_tool"]`, `invalid_regex`, `invalid_parameter`,
`not_found`) + `error` (Deutsch, modelllesbar).

---

## Tool Profiles

### main_chat (Vollzugriff)

```python
allowed_tools = [
    "web_search", "rag_search", "file_reader", "file_writer",
    "pdf_extract", "list_directory", "search_files", "code_executor", "calculator", "canvas",
]
fs_read = True, fs_write = True
max_file_size_mb = 2.0, max_search_depth = 5
```

### finance_tab (Read-only)

```python
allowed_tools = ["rag_search", "file_reader", "calculator"]
fs_read = True, fs_write = False  # Kein Schreiben!
max_file_size_mb = 5.0  # Größer für CSV/Excel
max_search_depth = 3
```

### psych_tab (Privacy-First)

```python
allowed_tools = ["rag_search"]  # Nur RAG
fs_read = False, fs_write = False  # Kein FS-Zugriff!
```

### settings_tab (Konfig-Lesen)

```python
allowed_tools = ["file_reader"]
fs_read = True, fs_write = False
max_file_size_mb = 1.0
```

---

## Security-Layer (path_sandbox.py)

| Schutz | Mechanismus | Quelle |
|--------|-------------|--------|
| Path Traversal | `os.path.realpath()` vor jedem Zugriff | SOTA 2026 |
| Symlink-Escape | `os.path.islink()` → abweisen | SOTA 2026 |
| Workspace-Boundary | Resolved path muss unter `workspace_root` liegen | SOTA 2026 |
| Binärdatei-Erkennung | Null-byte Heuristik (erste 8192 Bytes) | Eigene Implementierung |
| Depth-Limiter | Standard: 5 Ebenen | Konfigurierbar |
| Char-Limiter | Standard: 50.000 Zeichen | Token-Budget-Schutz |
| Size-Limiter | Standard: 20 MB (`policy.max_read_bytes`) | Konfigurierbar (P1: 50 → 20 MB) |
| Ergebnis-Cap (P2) | `max_results` Default 50, Hard-Max 200 | rg-Backend (`--max-count`) |
| Timeout (P2) | Default 10 s, Hard-Max 60 s → `partial`/`timed_out` | rg-Backend (hartes Kill) |
| Hidden-Dateien (P2) | Aus (Default `hidden=false`), `.gitignore` wird beachtet | rg-Backend |
| Datei-Größe (P2) | max. 20 MB/Datei (`MAX_RG_FILE_SIZE_BYTES`) | rg-Backend (`--max-filesize`) |

---

## Tests

| Datei | Tests | Abdeckung |
|-------|-------|-----------|
| `tests/test_path_sandbox_sota.py` | 17 | Symlink, Path Traversal, Binary, Depth, Oversize |
| `tests/test_tool_profiles.py` | 14 | Profile, Filter, FS-Read/Write pro Tab |
| `tests/test_filesystem_tools_integration.py` | 11 | Tool-Schemas, Dispatch, End-to-End |
| `tests/test_file_reader_safety.py` | 7 | P0: Trunkierung, Binary, Sandbox-Escape, UTF-8, Vertrag |
| `tests/test_file_reader_offset_limit.py` | 25 | P1: offset/limit, Navigation, EOF, Byte-Guard, Schemas |
| `tests/test_search_files_rg.py` | 26 | P2: rg-Content-Suche, Caps/Trunkierung, Timeout/Partial, fixed_string, hidden, context, Fallback, Sandbox-Vertrag |
| **Gesamt** | **100** | **Alle PASS (2026-08-25)** |

---

## SOTA-Bewertung

| Kriterium | Score | Bemerkung |
|-----------|-------|-----------|
| Path-Traversal-Schutz | 10/10 | `realpath()` + Boundary-Check |
| Symlink-Schutz | 10/10 | `islink()` vor jedem Zugriff |
| Declarative Config | 9/10 | Python-Dataclass statt YAML (einfacher) |
| Binary-Erkennung | 8/10 | Heuristisch (null-bytes), kein Magic-Bytes-DB |
| Performance | 10/10 | ripgrep-Backend (parallel, capped, P2) + keine neuen pip-Abhängigkeiten |
| **Gesamt** | **9.4/10** | **SOTA-Niveau** |

---

## Changelog

| Datum | Änderung |
|-------|----------|
| 2026-08-25 | **P2: ripgrep-Content-Suche:** `search_files` = Default-Content-Suche via `rg --json` (Cap 50/200, Timeout 10 s/60 s, `fixed_string`, `case_insensitive`, `glob`, `hidden`, `context`, `max_results`, `timeout`); Python-Name-Suche als Fallback (kein rg / rg-Fehler / Timeout) mit identischem Fehler-Vertrag. 26 neue Tests (`test_search_files_rg.py`), Full-Suite 808/808 PASS. Workdoc: `docs_archive/WORKDOC_FILESYSTEM_CONTEXT_SAFETY_20260824.md`. |
| 2026-08-24 | **P0 + P0.5 + P1:** `file_reader` → `read_file_safe()` (Hard-Char-Limit 50k, Binary-Check) + `offset`/`limit` (2000-Zeilenfenster, `next_offset`-Navigation, Byte-Guard 20 MB) + `list_directory` `max_depth` Default 2 + Tool-Result-Eviction (`agent/tool_result_eviction.py`, Threshold 3.000 Tokens). 39 neue Tests, Full-Suite 782/782 PASS. |
| 2026-08-07 | **Prompt-Integration:** `PLANNER_SYSTEM` um Regeln 8–9 erweitert (list_directory, search_files). `PLANNER_USER_TEMPLATE` um beide Tools ergänzt. Nummerierung korrigiert (1–14). |
| 2026-08-05 | Phasen 1–6 vollständig implementiert, 38 Tests PASS |

## Referenzen

- [Masters of AI Harness](https://github.com/nicepkg/harness) — Declarative Tool Filtering
- [dev.to: Tool Routing 2026](https://dev.to) — Router-Orchestrator-Pattern
- AGENTS.md — PowerShell-Syntax, GPU-Parameter, Konventionen
