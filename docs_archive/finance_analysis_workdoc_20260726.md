# Finance-Tab Analyse - Workdoc

**Datum:** 2026-07-26
**Status:** Analyse abgeschlossen, Fixes pending

## User-Prompt (Original)

Analysiere bitte den Finance-Tab: gibt es Fehler im Code oder Unlogiken in den Funktionen?

## Modul-Übersicht (13 Dateien, ~9.5K Zeilen)

| Datei | Zeilen | Status |
|-------|--------|--------|
| `grammar_compiler.py` | 725 | ✅ Analyzed |
| `query_planner.py` | 394 | ✅ Analyzed |
| `query_reflector.py` | 266 | ✅ Analyzed |
| `chat.py` | 879 | ✅ Analyzed |
| `tools.py` | 942 | ✅ Analyzed |
| `db_schema.py` | 3426 | ✅ Analyzed |
| `cache.py` | 207 | ✅ Analyzed |
| `categorizer.py` | 309 | ✅ Analyzed |
| `models.py` | 572 | ✅ Analyzed |
| `extractor.py` | 1545 | ✅ Analyzed |
| `token_budget.py` | 40 | ✅ Analyzed |
| `tab.py` | 1360 | ✅ Analyzed |
| `__init__.py` | 34 | ✅ Analyzed |

## Gefundene Issues

### CRITICAL

#### C1: `_to_cents()` Round-Htrip Inconsistency (db_schema.py:67-75)
**Severity:** Critical - Datenkorruption
**Location:** `finance/db_schema.py`
**Problem:** `_to_cents(10.5)` = 1050 (korrekt), aber `_to_cents(10.505)` = 1050 statt 1051. Bankendepends auf exakte Cent-Genauigkeit.
**Root Cause:** `int(amount * 100)` verliert Präzision bei float*int Multiplikation.
**Fix:** `Decimal(str(amount)) * 100` mit QUANTIZE.

#### C2: `_from_cents()` Round-Trip Inconsistency (db_schema.py:77-84)
**Severity:** Critical - Datenkorruption
**Location:** `finance/db_schema.py`
**Problem:** `_from_cents(1050)` = 10.5 (korrekt), aber `_from_cents(105)` = 1.0 statt 1.05.
**Root Cause:** `int(cents / 100)` ist GANZZAHLIGER Integer-Divisions-Effekt (floor division Verhalten).
**Fix:** `cents / 100.0` (float division).

#### C3: `_to_cents()` None Handling (db_schema.py:67-75)
**Severity:** Critical - RuntimeCrash
**Location:** `finance/db_schema.py`
**Problem:** `_to_cents(None)` wirft TypeError statt 0 zu returnen.
**Root Cause:** `None * 100` ist invalid.
**Fix:** `_to_cents(0 or amount)` oder explizite None-Check.

### HIGH

#### H1: `_format_eur()` String-Manipulation Bug (tab.py:92-93)
**Severity:** High - UI Display Error
**Location:** `finance/tab.py`
**Problem:** `_format_eur(1234.56)` = "1.234,56 " (trailing space!). `_format_eur(0.005)` = "0,00 " statt "0,01".
**Root Cause:** `f"{value:,.2f} "` hat trailing space, dann `.replace(",", "X").replace(".", ",").replace("X", ".)` - die Reihenfolge ist korrekt, aber das trailing space bleibt.
**Impact:** Alle UI-Displays zeigen Beträge mit trailing space.

#### H2: Silent Fallback in `_to_cents()` (db_schema.py:77-84)
**Severity:** High - Against Project Convention
**Location:** `finance/db_schema.py`
**Problem:** `except (ValueError, TypeError): return 0` unterdrückt alle Exceptions.
**Root Cause:** Broad except clause.
**Fix:** Spezifische Exception-Types, oder gar kein Fallback (project convention: "no silent fallbacks").

#### H3: `_render_chat_tab()` History Append on Error (tab.py:1305-1312)
**Severity:** High - Data Integrity
**Location:** `finance/tab.py`
**Problem:** Assistant message wird IMMER an history angehängt, selbst wenn `engine.respond()` eine Exception wirft.
**Root Cause:** `history.append(...)` steht außerhalb des try/except.
**Fix:** In try/except einbetten.

#### H4: `FinanceChatEngine.respond()` Tool-Call Injection (chat.py)
**Severity:** High - Security
**Location:** `finance/chat.py`
**Problem:** Wenn `allow_python=True`, kann das LLM via `code_executor` beliebigen Python-Code ausführen.
**Root Cause:** Keine Sandbox/Whitelist für code_executor.
**Mitigation:** Bereits im Caption dokumentiert ("kein Internet"), aber keine technische Enforce.

### MEDIUM

#### M1: `resolve_context_tokens()` Magic Number (token_budget.py:28)
**Severity:** Medium - Maintenance
**Location:** `finance/token_budget.py`
**Problem:** `value > 1024` ist magic number ohne Kommentar.
**Fix:** Konstante `MIN_VALID_CONTEXT = 1024` mit Docstring.

#### M2: `_chunk_markdown()` Docstring Typo (extractor.py:1489)
**Severity:** Medium - Documentation
**Location:** `finance/extractor.py`
**Problem:** "Teilt Markdown in ueberlappende Windows auf." - "Windows" statt "Chunks".
**Fix:** Typo korrigieren.

#### M3: `FinanceExtractor._resolve_booking_date()` Date Fallback (extractor.py:1466-1485)
**Severity:** Medium - Logic
**Location:** `finance/extractor.py`
**Problem:** Wenn `in_range` leer und `end is None`, return `candidates[0]` - das kann ein sehr altes/neues Datum sein.
**Root Cause:** Keine Validierung, ob candidates überhaupt sinnvoll sind.
**Fix:** Warnlog wenn Fallback verwendet wird.

#### M4: `_render_transfers_tab()` Re-Link Uses Wrong Variable (tab.py:1061)
**Severity:** Medium - Logic Bug
**Location:** `finance/tab.py:1061`
**Problem:** `db.relink_all_transfers(max_days=max_days)` - `max_days` ist der UI-Slider (1-14 Tage), nicht das produktive Fenster (`gap_window`, 14-120 Tage).
**Root Cause:** Variable Name Collision. `max_days` kommt von `cols[0].slider(..., key="finance_link_window")` (Zeile 975-978), während `gap_window` das richtige Fenster ist.
**Fix:** `db.relink_all_transfers(max_days=gap_window)` verwenden.

### LOW

#### L1: Duplicate `import pandas as pd` (tab.py:266, 332, 370, 405, 447, 486, 514, 614, 664, 680, 768, 851, 937)
**Severity:** Low - Code Quality
**Location:** `finance/tab.py`
**Problem:** pandas wird in fast jeder Funktion neu importiert.
**Root Cause:** Developer-Style (lazy imports pro Funktion).
**Fix:** Module-level import (bereits Zeile 23 vorhanden, aber nicht konsistent genutzt).

#### L2: `_safe_int()` Type Ignore (tab.py:66)
**Severity:** Low - Type Safety
**Location:** `finance/tab.py`
**Problem:** `return int(scalar)  # type: ignore[arg-type]` unterdrückt mypy-Warnung.
**Fix:** Bessere Type Guards.

#### L3: `FinanceDB.get_instance()` Singleton Thread-Safety (db_schema.py)
**Severity:** Low - Concurrency
**Location:** `finance/db_schema.py`
**Problem:** Singleton ohne Lock. Bei parallelen Streamlit-Reruns könnte es zu Race Conditions kommen.
**Fix:** Thread-local storage oder Lock.

## SOTA Vergleich

| Bereich | Current | SOTA | Gap |
|---------|---------|------|-----|
| GGUF Inference | llama-cpp-python, n_batch=3072 | llama.cpp v0.x mit KV-Cache Quantization (Q4_K_M) | Medium |
| RAG Pipeline | Docling + FAISS | LlamaIndex mit RecursiveUrlLoader + Hybrid Search | Low |
| Query Planning | Grammar-based (GBNF) | ReAct + Toolformer mit Self-Correction | Medium |
| Caching | Simple LRU | Semantic Caching (Embedding-basiert) | High |
| Token Budget | Manual resolution | Dynamic via model metadata API | Medium |
| Chunking | Fixed-size with boundary detection | Semantic/Agentic Chunking | Medium |
| Error Handling | Silent fallbacks | Structured Error Reporting mit Retry | High |

## Next Steps

1. [ ] Backup erstellen
2. [ ] C1-C3 Fixen (`_to_cents`, `_from_cents`)
3. [ ] H1-H4 Fixen
4. [ ] M1-M4 Fixen
5. [ ] L1-L3 Cleanup
6. [ ] Tests ausführen
7. [ ] Dokumentation aktualisieren
8. [ ] Workdoc löschen