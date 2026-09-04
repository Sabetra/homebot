# Workdoc: Filesystem Context-Safety (P0–P2)

> **Erstellt:** 2026-08-24
> **Status:** P0 + P0.5 + P1 + P2 **ABGESCHLOSSEN** (2026-08-25)
> **Autor:** Cline (Agent)
> **Reviewer:** User

---

## Original-Auftrag

Build a local-first, SOTA-aligned filesystem/tool stack for the chatbot:
fix the production context-overflow bug in `file_reader` (P0), add bounded
search/read navigation (P1 line-based offset/limit, P2 ripgrep-based
`search_files`), add orchestrator-level eviction of old idempotent tool
results (P0.5), and keep P5 (semantic search) / P6 (symbol/call-graph) as an
optional later intelligence layer. Zero new pip dependencies. Sources:
Anthropic tool-context-management docs, Claude Code Read/Grep behavior,
MCP filesystem server behavior (fetched 2026-08-24).

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| P0: `file_reader` → `read_file_safe` (50K-Char-Hard-Cap, Metadaten) | P5: semantic search (später, optional) |
| P0.5: Eviction alter Tool-Results (idempotent, last-K) | P6: Symbol-/Callgraph (später, optional) |
| P1: `file_reader` offset/limit (1-based, Default 2000) | Neue pip-Abhängigkeiten |
| P2: `search_files` via `rg --json` (Caps, Timeout, Partial) | Cloud-Indexierung, neue APIs |
| Doku-Fixes (17_FILESYSTEM_CONNECTOR.md, funktionen.md) | |
| Regressionstests für alle Phasen | |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | `file_reader` liest max. 50.000 Zeichen; größere Dateien liefern `was_truncated=true` + Metadaten | Test `test_large_file_truncated_and_flagged` | ☑ 2026-08-24 |
| 2 | Binary-/Sandbox-Ergebnisvertrag bleibt stabil (`error_class`, `needs_user_permission`) | Bestehende + neue Tests | ☑ 2026-08-24 |
| 3 | Orchestrator evictet alte idempotente Tool-Results (last-K=2), behält user/assistant/tool-call-Struktur | Neue Tests | ☑ 2026-08-24 (20/20 Eviction-Tests, Full-Suite 757/757) |
| 4 | `file_reader` akzeptiert `offset`/`limit` (1-based); liefert `start_line`/`end_line`/`has_more_lines`/`next_offset` | Neue Tests | ☑ 2026-08-25 (25/25 PASS, Full-Suite 782/782) |
| 5 | `search_files` liefert strukturierte Hits (file/line/snippet/context), Cap 50, Timeout 10s mit `partial`/`timed_out` | Neue Tests | ☑ 2026-08-25 (26/26 rg-Tests, Zielauswahl 139/139, Full-Suite 808/808) |
| 6 | `docs/17_FILESYSTEM_CONNECTOR.md` widerspricht nicht mehr dem Code (Char-Limiter, Size-Limiter) | Inspektion | ☑ 2026-08-24 |
| 7 | Bestehende Filesystem-/Orchestrator-Tests bleiben grün | Testlauf | ☑ 2026-08-24 (122/122) |

## Alternativen & Entscheidung (P0)

| # | Option | Pro | Contra | Entscheidung |
|---|--------|-----|--------|--------------|
| A | `file_reader` → bestehende `read_file_safe()` (4-tuple mit `total_chars`) | Root-Cause-Fix, nutzt bestehenden Sandbox-Pfad, Binary-Check + `errors="replace"` gratis | API-Änderung an `read_file_safe` (1 Test-Datei, 3 Unpack-Stellen) | **AUSGEWÄHLT** |
| B | `read_text()` behalten, Trunkierung in `_file_reader` nachträglich anwenden | Keine Sandbox-API-Änderung | Doppelte Logik (Size-/Char-Check), Binary-Check fehlt im Produktionspfad, `encoding_error`-Crash-Pfad bleibt | abgelehnt |
| C | Neue Funktion `read_text_capped()` in path_sandbox.py | Keine API-Brüche | Weitere parallele Read-Pfade → Duplikat von A, mehr Wartungsaufwand | abgelehnt |

> **Auswahl:** A — `read_file_safe` ist exakt der fehlende Produktionspfad;
> die einzige Call-Call-Site ist der Test (verifiziert via Codebase-Suche).

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|--------|
| 1 | `_file_reader` nutzt `read_text()` (50 MB, kein Char-Limit) | `agent_toolkit.py:2357-2387` |
| 2 | `read_file_safe()` existiert: Binary-Check, 50 MB, 50K-Char-Trunkierung, `errors="replace"` | `agent/path_sandbox.py:468-517` |
| 3 | `read_file_safe` nur in Tests aufgerufen (3 Stellen, 3-tuple) | `tests/test_path_sandbox_sota.py:114,122,129,166,173` |
| 4 | `PathSandboxPolicy()` mit Defaults; Profile `max_file_size_mb` nicht verdrahtet | `agent_toolkit.py:207`, `agent/tool_profiles.py:104` |
| 5 | `agent_chatbot_logic.py` liest `result["content"]` (Vertrag `success`+`content` stabil halten) | `agent_chatbot_logic.py:1569-1573` |
| 6 | Doku-Drift: Zeile 42 (read_text mit Char-Limiter — falsch), Zeile 137 (5 MB — falsch, Code: 50 MB) | `docs/17_FILESYSTEM_CONNECTOR.md` |
| 7 | `estimate_prompt_tokens()` verfügbar (für P0.5-Budget) | `utils/token_manager.py:57` |
| 8 | `rg` 14.1.0 unter `C:\Windows\System32\rg.exe` (für P2) | Umgebungs-Check 2026-08-24 |

## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | `read_file_safe` mit 4-tuple bricht keine weiteren Call-Sites | **bestätigt 2026-08-24** | Codebase-Suche: nur 2 Test-Stellen + neuer Produktions-Call; 49/49 Filesystem-Tests grün |
| 2 | Der ReAct-Node ist der korrekte Hook-Point für P0.5 („Orchestrator-Level") | **bestätigt 2026-08-24** | Code-Inspektion: `agent/orchestrator.py` (Planner→Tools→Summarizer) trägt KEIN `state["messages"]` mit Tool-Results über Iterationen (kein `tool_call_id`-Zustand, grep-verifiziert). `agent/react_agent.py` hingegen führt `state["messages"]` Iteration für Iteration (Zeile ~1253 → ~1475) — genau dort häufen sich die FS-Tool-Results auf. Produktionspfad: `agent_chatbot_logic._react_agent_chat` → `ReActAgent.run` |

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmaßnahme |
|---|--------|--------------------|------------|--------------------|
| 1 | `encoding_error`-Pfand verschwindet (errors="replace") — Verhalten weicht ab | niedrig | niedrig (Robustheitsgewinn) | Test dokumentiert neues Verhalten |
| 2 | Unbekannte Call-Site von `read_file_safe` (3-tuple) | niedrig | mittel | Codebase-Suche vor Änderung (Fakt #3) |
| 3 | LLM reagiert falsch auf `suggested_action`-Metadaten | niedrig | niedrig | Metadaten sind optional; `content` bleibt Hauptkanal |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `agent/path_sandbox.py` | `read_file_safe()` → 4-tuple `(path, content, was_truncated, total_chars)`; Docstring aktualisiert | 19/19 SOTA-Tests grün |
| 2 | `agent_toolkit.py` | `_file_reader`: `read_text()` → `read_file_safe()` (Hard-Char-Limit 50k, Binary-Check, `errors="replace"`) + Metadaten `total_chars`/`was_truncated`/`truncated_at`/`suggested_action` | 7/7 Safety-Tests grün |
| 3 | `agent/tool_schemas.py` | `file_reader`-Schema-Beschreibung: 50.000-Zeichen-Limit + Trunkierungs-Hinweis | Schema-Tests grün |
| 4 | `tests/test_path_sandbox_sota.py` | 2 Unpack-Stellen auf 4-tuple angepasst | 19/19 grün |
| 5 | `tests/test_file_reader_safety.py` | **NEU**: 7 Regressionstests (Trunkierung, Edge-Case 50k, Binary, Sandbox-Escape, UTF-8-Replace, Vertragsstabilität) | 7/7 grün |
| 6 | `docs/17_FILESYSTEM_CONNECTOR.md` | `last-verified` → 2026-08-24; Architektur-Diagramm (read_text ohne Char-Limit, read_file_safe Char-Limit); Size-Limit 5 MB → 50 MB; **neues Kapitel `file_reader`** (Parameter, Rückgabe, Limits, P0-Historie); Test-Statistik 38 → 45 | — |
| 7 | `agent/tool_result_eviction.py` | **P0.5 NEU:** `evict_stale_tool_results()` — ersetzt alte idempotente FS-Tool-Results (`file_reader`, `search_files`, `list_directory`) durch `[EVICTED]`-Platzhalter; last-K=2 pro Tool intakt; Trigger ≥ 3.000 Tokens (`utils/token_manager.estimate_prompt_tokens`, `use_tiktoken=False`); reine Funktion (Input nie mutiert), idempotenter Zweidurchlauf | 17/17 PASS |
| 8 | `agent/react_agent.py` | **P0.5:** Non-fataler Hook in `_node_agent_step` direkt vor `model_loader.generate_with_tools()` (~Zeile 1361); try/except + `logger.warning` — Eviction-Fehler stoppen den Chat NICHT | 3/3 PASS (Integration) |
| 9 | `tests/test_tool_result_eviction.py` | **P0.5 NEU:** 17 Unit-Tests (last-K, Allowlist, Nicht-Mutation, Idempotenz, Token-Trigger, Stats, Platzhalter-Vertrag) | 17/17 PASS |
| 10 | `tests/test_react_agent_eviction_integration.py` | **P0.5 NEU:** 3 Integrationstests im echten `_node_agent_step`-Node mit Stub-ModelLoader (Eviction aktiv/inaktiv/non-fatal) | 3/3 PASS |
| 11 | `funktionen.md` | **P0.5 NEU:** Abschnitt R „Tool-Result Eviction“ (Kern-Komponenten, Design-Prinzipien); Zeilen-/Namens-Details gegen Code verifiziert | — |

## Änderungen (P1)

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `agent/path_sandbox.py` | **P1:** `read_file_safe(offset, limit)` — Zeilenfenster (1-basiert; `offset` Default 1, `limit` Default `DEFAULT_READ_LINE_LIMIT = 2000`); Rückgabe 4→5-tuple, 5. Element `line_meta` = `{total_lines, start_line, end_line, has_more_lines, next_offset}`; `next_offset` immer int (`max(start_line, end_line + 1)`; Voll-Read → `total_lines + 1`, Offset über EOF → Offset selbst); Byte-Guard `DEFAULT_MAX_READ_BYTES` 50 → 20 MB (Token-Budget-Schutz), Fehler "Datei größer als Limit: …" | 25/25 (offset/limit) |
| 2 | `agent_toolkit.py` | **P1:** `_file_reader` akzeptiert `offset`/`limit`; `line_meta` 1:1 in JSON-Result geflattet (`total_lines`, `start_line`, `end_line`, `has_more_lines`, `next_offset`); `suggested_action`: Weiterlesen-Hinweis mit konkretem `next_offset` (Teil-Read), Char-Backstop-Warnung (Byte-Read), EOF-Überlauf-Hinweis ("Nutze `offset` ≤ N") | 25/25 |
| 3 | `agent/tool_schemas.py` | **P1:** `file_reader`-Schema um `offset`/`limit`-Parameter + Continuation-Hinweis (Navigation über `next_offset`) erweitert | Schema-Tests |
| 4 | `tests/test_file_reader_offset_limit.py` | **P1 NEU:** 25 Tests (Fenster-Basisfälle, Continuation via `next_offset`, End-to-End, `limit`-Clamping, `line_meta`-Vertrag inkl. `end_line=0` bei leerem Fenster, EOF-Überlauf, leere Datei, 60k-Char-P0-Regression, Byte-Guard 20 MB, Sandbox-Escape, UTF-8-Replace, Toolkit-E2E, Schema-Parameter) | 25/25 PASS |
| 5 | `tests/test_path_sandbox_sota.py` + `tests/test_file_reader_safety.py` | **P1:** Unpack-Stellen auf 5-tuple/`line_meta` angepasst; Byte-Guard-Test 50 → 20 MB + Match "größer als" | 20/20 PASS |
| 6 | `docs/17_FILESYSTEM_CONNECTOR.md` | **P1:** `file_reader`-Kapitel um `offset`/`limit` + `line_meta`-Metadaten (inkl. `next_offset`-Vertrag) erweitert; Size-Limit 50 → 20 MB; Test-Statistik +25 | — |
| 7 | `agent/agent_toolkit.py` (Fragment) | **P1-Umgebung:** 35-Zeilen-Tool-Wrapper-Fragment außerhalb des P1-Scope entfernt (doppelter `import agent.path_sandbox` — hätte die Sandbox unter `agent/agent/path_sandbox.py` aufgelöst); Deletion von außen erkannt, per Autosave `d30ff2cc` committet | — |

### P1-Design-Prinzipien

- **Claude-Code-Read-Modell:** Fenster-Read statt Voll-Read — das Modell steuert die
  Navigation über `next_offset` (deterministisch, keine Zeilen-Zählerei des LLM).
- **`next_offset` immer int:** Eindeutiger Fortsetzungs-Punkt ohne `None`-Ämbiguität;
  Offset über EOF → der Offset selbst (leeres Fenster, `has_more_lines=false`) →
  `suggested_action` korrigiert das Limit ("Nutze `offset` ≤ N").
- **Schicht-Stack:** 20-MB-Byte-Guard (hart, `sandbox_error`) → 2000-Zeilenfenster →
  50K-Char-Backstop (weich, `was_truncated` + `truncated_at`) — jede Schicht unabhängig
  testbar; das P0-Char-Limit bleibt als Backstop für sehr lange Zeilen erhalten.
- **Kooperation mit P0.5:** Evictierte `file_reader`-Ergebnisse (P0.5-Platzhalter) werden
  jetzt gezielt per `offset`/`limit` nachgeladen statt per kostspieligem Voll-Read.

## Änderungen (P2)

> **P2: ripgrep-Content-Suche — ABGESCHLOSSEN 2026-08-25.**
> `search_files` ist jetzt Default-Content-Suche im Dateiinhalt; Name-Suche via
> `content_search=false`. Keine neue pip-Abhängigkeit (rg-Binary, lokal;
> Python-Fallback ohne rg).

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `agent/path_sandbox.py` | **P2 NEU:** `PathSandbox.search_content_rg()` — `rg --json`-Content-Suche: `--max-count` (Cap), `--max-filesize` (20 MB), `--no-hidden`/`--hidden` + `.gitignore`, `--context` (0–100), `-F` (fixed_string), `-i`, `--glob`; Exit-Codes 0/1/2 interpretiert; Timeout → `SIGKILL` + bereits geparste Hits bleiben erhalten (`partial`/`timed_out`); Konstanten `DEFAULT_RG_TIMEOUT=10.0`, `MAX_RG_TIMEOUT=60.0`, `DEFAULT_RG_MAX_RESULTS=50`, `MAX_RG_MAX_RESULTS=200`, `MAX_RG_FILE_SIZE_BYTES=20MB` | 26/26 rg-Tests |
| 2 | `agent_toolkit.py` | **P2:** `_search_files()` umgeschrieben — rg-first-Dispatch, Fallback-Kette (kein rg / `FileNotFoundError`/`OSError` / rg-Exit-2 / Timeout → Python-Name-Suche via `search_files_safe`), stabilisierter Fehler-Vertrag (`success`/`error_code`/`error`; `sandbox_error` → `needs_user_permission=true` + `allowed_tools=["execute_tool"]`), `invalid_regex`, `invalid_parameter`; `ImportError`-Schutz für rg-Konstanten | 26/26 rg-Tests |
| 3 | `agent/tool_schemas.py` | **P2:** `search_files`-Schema auf Vollvertrag: 8 Parameter (`root_path`, `pattern`, `content_search` Default `true`, `case_insensitive`, `fixed_string`, `glob`, `hidden`, `context`, `max_results`, `timeout`) + Rückgabe-Vertrag + Fehler-Vertrag in Beschreibung | Schema-Test grün |
| 4 | `agent/prompts.py` | **P2:** Regel 9 `PLANNER_SYSTEM` (Content-Suche Default, Name-Suche via `content_search=false`, `fixed_string`/`glob`/`context`), WICHTIG-Liste (search_files-Zeile → Content-Suche), `PLANNER_USER_TEMPLATE` (search_files-Zeile → `content_search`, `fixed_string`, `case_insensitive`) | Guard-/Planner-Tests grün |
| 5 | `tests/test_search_files_rg.py` | **P2 NEU:** 26 Tests (rg-Content-Hits, Caps/Trunkierung, Timeout-Partial, fixed_string, case_insensitive, hidden on/off, context, invalid_regex, Name-Fallback `content_search=false`, rg-missing-Fallback (Shim `shutil.which`), Sandbox-Abweisung, Permission-Shape, `success`/`error_code`-Vertrag, leere Suche) | 26/26 PASS |
| 6 | `docs/17_FILESYSTEM_CONNECTOR.md` | **P2:** `search_files`-Kapitel auf Vollvertrag (8 Parameter, Rückgabe, Fehler-Vertrag, rg-Limits), Architektur-Diagramm (rg-Path + Fallback), Security-Layer +4 rg-Rows, Test-Tabelle 74 → 100, Changelog +2 Einträge, SOTA Performance 9 → 10, `last-verified` → 2026-08-25 | — |
| 7 | `funktionen.md` | **P2:** Abschnitt P — Stand 2026-08-25, Tests 38 → 100, Kern-Komponenten 5/11/13 aktualisiert, neue Zeile 18 (`search_content_rg` + rg-Konstanten), neuer Unterabschnitt „P2: ripgrep-Content-Suche“ | — |

### P2-Design-Prinzipien

- **rg-first, Python-Fallback:** ripgrep ist parallel, memory-mapped und
  `.gitignore`-aware → klarer SOTA-Vorteil; der Python-Pfad bleibt als
  deterministischer Fallback (kein rg, rg-Fehler, Timeout) mit identischem
  Fehler-Vertrag — kein Feature-Verlust, nur andere `match_type`.
- **`fixed_string` als Default-Empfehlung:** Regelsuche bleibt Regex;
  LLMs sollten für exakte Strings `fixed_string=true` senden (→ `rg -F`,
  kein Regex-Parser → keine Injection, kein Backtracking, schneller).
- **Sandbox vor Exec:** `root_path` läuft erst durch `PathSandbox.resolve()`;
  `search_content_rg` akzeptiert nur absolute, im Workspace liegende Pfade —
  die rg-Argumente sind nie ein zweiter Pfad-Kanal.
- **Partial statt Fail:** Timeout tötet den rg-Prozess hart (SIGKILL),
  liefert aber die bereits geparsten Hits mit `partial=true` + `timed_out=true`
  — deterministisch verwertbar für das Modell (weiter mit schmalerem Scope).
- **Vertragsstabilität:** `success`/`error_code`/`error` +
  `needs_user_permission`/`allowed_tools` bleiben über alle Pfade
  (rg, Fallback, Sandbox-Abweisung) unverändert — `execute_tool`-Allowlist
  braucht keine Änderung.

## Rollback-Strategie

| Schritt | Aktion | Befehl / Referenz |
|---------|--------|-------------------|
| 1 | Backups (aktueller Zustand) liegen in `%USERPROFILE%ot6_backups\20260824_filesystem_context_safety\` | Copy-Item zurück |
| 2 | **P0:** Dateien auf Zustand vor P0 zurücksetzen (Commit `8ed36c0a`, 22:57 — letzter Snapshot vor `31c3c46a`) | `git -C <PROJEKT_ROOT> checkout 8ed36c0a -- agent/path_sandbox.py agent_toolkit.py agent/tool_schemas.py tests/test_path_sandbox_sota.py docs/17_FILESYSTEM_CONNECTOR.md` + `Remove-Item tests/test_file_reader_safety.py` |
| 3 | **P0.5:** Hook in `react_agent.py` entfernen (Zustand vor `c9041d09` = Commit `31c3c46a`, 23:07) | `git -C <PROJEKT_ROOT> checkout 31c3c46a -- agent/react_agent.py` |
| 4 | **P0.5:** Neue Dateien löschen | `Remove-Item agent/tool_result_eviction.py tests/test_tool_result_eviction.py tests/test_react_agent_eviction_integration.py` |
| 5 | **P0.5:** funktionen.md-Abschnitt R entfernen (Zustand vor `a1ab004f` = Commit `f1a13f79`, 21:17) | `git -C <PROJEKT_ROOT> checkout f1a13f79 -- funktionen.md` |
| 6 | **P1:** Dateien auf Zustand vor P1 zurücksetzen (Commit `e708b177`, 23:47 — letzter Snapshot vor `6f06a064`) | `git -C <PROJEKT_ROOT> checkout e708b177 -- agent/path_sandbox.py agent_toolkit.py agent/tool_schemas.py tests/test_path_sandbox_sota.py tests/test_file_reader_safety.py docs/17_FILESYSTEM_CONNECTOR.md` + `Remove-Item tests/test_file_reader_offset_limit.py` |
| 7 | **P1:** funktionen.md-Abschnitt S entfernen (Zustand vor der P1-Doku = Commit `1e209e9b`) | `git -C <PROJEKT_ROOT> checkout 1e209e9b -- funktionen.md` |
| 8 | Workdoc (nur zu diesem Task) bei Voll-Rollback löschen | `Remove-Item docs/WORKDOC_FILESYSTEM_CONTEXT_SAFETY_20260824.md` |
| 9 | **Hinweis:** Alle P0/P0.5/P1-Änderungen sind per Autosave committet (`31c3c46a`–`1e209e9b`); `git checkout -- <datei>` **ohne SHA** wäre ein No-Op. Außen-Artefakt `scripts/tmp_check_toolkit.py` (extern erstellt, 2026-08-25) bei Bedarf manuell löschen | Verlauf: `git -C <PROJEKT_ROOT> log --oneline -- <datei>` |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | `tests/test_file_reader_safety.py` (7 Tests, neu) | 7/7 PASS | 2026-08-24 |
| 2 | `tests/test_path_sandbox_sota.py` (19 Tests) | 19/19 PASS | 2026-08-24 |
| 3 | `tests/test_filesystem_tools_integration.py` (10 Tests) | 10/10 PASS | 2026-08-24 |
| 4 | `tests/test_tool_profiles.py` (15 Tests) | 15/15 PASS | 2026-08-24 |
| 5 | `tests/test_tool_profile_gating.py` + `tests/test_tool_retriever.py` (73 Tests) | 73/73 PASS | 2026-08-24 |
| 6 | `py_compile` aller 5 geänderten `.py`-Dateien | OK | 2026-08-24 |
| 7 | **P0.5:** `tests/test_tool_result_eviction.py` (17 Tests, neu) | 17/17 PASS | 2026-08-24 |
| 8 | **P0.5:** `tests/test_react_agent_eviction_integration.py` (3 Tests, neu) | 3/3 PASS | 2026-08-24 |
| 9 | **P0.5:** Zielselektion (ReAct/FS/Guard-Suites) | 72/72 PASS | 2026-08-24 |
| 10 | **P0.5:** `tests/` vollständige Suite | **757/757 PASS** | 2026-08-24 |
| 11 | **P1:** `tests/test_file_reader_offset_limit.py` (25 Tests, neu) | 25/25 PASS | 2026-08-25 |
| 12 | **P1:** `tests/test_path_sandbox_sota.py` (12) + `tests/test_file_reader_safety.py` (8) — 5-tuple/`line_meta`-Vertrag | 20/20 PASS | 2026-08-25 |
| 13 | **P1:** Zielselektion (FS/ReAct/Guard-Suites) | 208/208 PASS | 2026-08-25 |
| 14 | **P1:** `tests/` vollständige Suite | **782/782 PASS** | 2026-08-25 |
| 15 | **P2:** `tests/test_search_files_rg.py` (26 Tests, neu) | 26/26 PASS | 2026-08-25 |
| 16 | **P2:** Zielauswahl (FS-Suites, path_sandbox, ReAct, Eviction, rg, Guard) | **139/139 PASS** | 2026-08-25 |
| 17 | **P2:** `tests/` vollständige Suite | **808/808 PASS** | 2026-08-25 |
| 18 | **P2:** `py_compile` agent_toolkit.py, agent/path_sandbox.py, agent/tool_schemas.py, agent/prompts.py | OK | 2026-08-25 |

> **Known-Change (bewusst):** Ungültiges UTF-8 in Textdateien wirft jetzt
> keinen `encoding_error` mehr, sondern wird mit U+FFFD decodiert
> (`errors="replace"` in `read_file_safe`). NUL-Bytes → weiterhin
> `sandbox_error` (Binary-Erkennung, dokumentiert).

---

> **Archiv-Status (2026-08-25):** Alle Phasen (P0, P0.5, P1, P2) sind
> abgeschlossen — gemäß AGENTS.md („Arbeitsdokumente nach Abschluss … nach
> `docs_archive/` verschieben“) wurde dieses Workdoc nach `docs_archive/`
> verschoben. Offen bleiben: P5 (semantische Suche) und P6 (Symbol-/Callgraph)
> als optionale Spätschicht (nicht im Scope dieses Workdocs).
