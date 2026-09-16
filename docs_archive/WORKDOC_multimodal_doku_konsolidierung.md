# Workdoc: Multimodal-RAG-Doku-Konsolidierung (funktionen.md)

> **Erstellt:** 2026-09-15
> **Abschluss-Ziel:** 2026-09-15
> **Status:** IN_ARBEIT
> **Autor:** Cline (AI-Agent)

---

## Original-Auftrag
*(Zusammenfassung aus dem Session-Kontext; verbatim-Auftragstext nicht verfügbar.)*

> "Consolidate duplicate and outdated `agent/multimodal_rag.py` documentation in `funktionen.md` against verified code behavior."
> Zusätzlich aus dem Kontext: docling_parallel.py-Referenzen korrigieren (Alias, `process_batch_async`, Zeilen), Zeilenzahl-Tabelle in `docs/01_ARCHITECTURE_DEEP_DIVE.md` fixen, danach Commit & Push.

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| `funktionen.md`: 5 multimodale Duplikat-Sektionen zu 1 konsolidieren | Code-Änderungen (reine Doku-Aufgabe) |
| `funktionen.md`: Nummerierung 17–22 konsistent machen | Sektionen 1–16 von funktionen.md |
| `funktionen.md`: docling_parallel.py-Referenzen (Zeilen, Alias, Fallback-Tiefe) | venv-/Dependency-Änderungen |
| `funktionen.md`: Zeilenkorrekturen in Lifecycle- & LangGraph-Sektion (gleiche Region) | |
| `docs/01_ARCHITECTURE_DEEP_DIVE.md`: Zeilenzahl-Tabelle (Module-Tabelle) | |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Genau 1 `##`-Sektion zu multimodal_rag.py in funktionen.md | grep `^## .*multimodal` | ☐ |
| 2 | Alle Zeilenverweise der konsolidierten Sektion = Code-Wirklichkeit | Abgleich mit grep multimodal_rag.py | ☐ |
| 3 | Keine Referenzen auf nicht existierende APIs (`SemanticChunker`, `to_latex`, `add_sub_chunk`, `from_dict`, `PsychSessionState`, `psychological_sessions`) | grep | ☐ |
| 4 | Nummerierung 17–22 fortlaufend, keine Duplikate | Read-back Region 680–1340 | ☐ |
| 5 | docling-Sektion: Alias Zeile 685, `process_batch_async` Zeile 568, 2-Tier-Fallback | Read-back | ☐ |
| 6 | Zeilenzahlen in 01_ARCHITECTURE_DEEP_DIVE.md = wc-Ergebnis | Abgleich | ☐ |
| 7 | Backups in `~\homebot_backups\` (byte-identisch) | getsize-Vergleich | ☑ |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Entscheidung |
|---|--------|-----|--------|--------------|
| A | Konsolidierung zu 1 Sektion (nur verifizierte Fakten), Duplikate löschen | Single Source of Truth, weniger Widersprüche, kleinere Datei | mehr Edits | **gewählt** |
| B | Alle 5 Sektionen behalten, 4 als "veraltet" markieren | minimaler Aufwand | Widersprüche bleiben, Agents irreführend | verworfen |

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|------------------------------|
| 1 | multimodal_rag.py = 706 Zeilen | `wc` |
| 2 | 5 Indizes `_index/_type_index/_source_index/_page_index/_hash_index` | multimodal_rag.py:448–453 |
| 3 | Overlap = `max(1, n//4)` Sätze (≈25 %) | multimodal_rag.py:243 |
| 4 | Satz-Split-Regex `(?<=[.!?])\s+(?=[A-Z\xC0-\xD6\xD8-\xDE])` | multimodal_rag.py:259 |
| 5 | `ContentType`: 7 Mitglieder (TEXT, TABLE, FIGURE, FORMULA, HEADER, CODE, MIXED) | multimodal_rag.py:18–24 |
| 6 | KEIN `from_dict`, `add_sub_chunk`, `to_latex`, `SemanticChunker`, `ChunkSubType`, `_is_header` (grep 0 Treffer) | multimodal_rag.py |
| 7 | `TableStructure`: nur Properties `markdown_export` (64), `natural_language` (80) | multimodal_rag.py:34–90 |
| 8 | Alias `MultimodalRAG = MultiModalRAG` Zeile 643 | multimodal_rag.py:643 |
| 9 | Dataclasses, nicht Pydantic | multimodal_rag.py:14–131 |
| 10 | docling: `process_batch_async` Zeile 568, Alias `DoclingParallel` Zeile 685, Datei 685 Zeilen | docling_parallel.py |
| 11 | docling: 2-Tier-PDF-Fallback (Docling → pdfplumber), kein AdvancedPDFProcessor | docling_parallel.py:390–432 |
| 12 | change_detector = 709 Zeilen (Doku sagte ~662) | `wc` |
| 13 | strixkat_eval = 1650 Zeilen; sota_pipeline = 580 Zeilen | `wc` |
| 14 | langgraph: State = `WellbeingSessionState` (Zeile 156), `_DependencyRegistry` (104), `_get_dep` (204) | langgraph_real.py |
| 15 | Session-Tabelle = `wellbeing_sessions`, Message-Tabelle = `session_interactions`, Insights = `wellbeing_insights` | async_db_operations.py, session_context_builder.py |
| 16 | WAL wird über `database/connection_pool.py` gesetzt (journal_mode) | grep |
| 17 | sota_pipeline nutzt MultimodalRAG: Import 39, Instanz 142, expand_query 679/727 | sota_pipeline.py |

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmaßnahme | Status |
|---|--------|--------------------|------------|--------------------|--------|
| 1 | Falsches `old_text` → Edit schlägt fehl/bricht Datei | niedrig | hoch | Frischer Read direkt vor jedem Edit; Read-back danach | offen |
| 2 | Dateikorruption durch abgebrochenen Großschreibvorgang | niedrig | hoch | Kleine chirurgische Edits, nie Gesamtdatei neu ausgeben | offen |
| 3 | Verlust noch gültiger Infos bei Duplikat-Löschung | mittel | mittel | Konsolidierte Sektion enthält alle verifizierten Fakten aller 5 Sektionen | offen |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `~\homebot_backups\` | Backup funktionen.md + 01_ARCHITECTURE_DEEP_DIVE.md (2026-09-15.bak) | getsize identisch (162399 / 11499) |
| 2 | WORKDOC | Workdoc erstellt | – |
| 3 | `funktionen.md` | Sektionen 19–22 konsolidiert (5 redundante multimodal-Sektionen → 4 verifizierte Sektionen 19–22); Splice via Skript (9 Anchor-Prüfungen OK, 158749→128076 Zeichen) | OK |
| 4 | `funktionen.md` | Sektion 11: Tabellennamen `psychological_*` → `wellbeing_*`; Sektion 18: 662→709 Zeilen + verwaister docling-SOTA-Block entfernt; Sektion 3.1: `StrixKATEval.evaluate()` (Zeile 853); Sektion 4.1: echte `SOTAPipeline.process_document()` (Zeile 229); `last-verified` → 2026-09-16 | OK |
| 5 | `docs/01_ARCHITECTURE_DEEP_DIVE.md` | Zeilenzahl-Tabelle korrigiert (change_detector 709, docling 685, multimodal 706, strixkat 1650, sota 580) | OK |

## Rollback-Strategie

| Schritt | Aktion | Befehl / Referenz |
|---------|--------|-------------------|
| 1 | funktionen.md wiederherstellen | `Copy-Item ~\homebot_backups\funktionen.md.2026-09-15.bak c:\Users\bot6\funktionen.md -Force` |
| 2 | Architekturdoku wiederherstellen | `Copy-Item ~\homebot_backups\01_ARCHITECTURE_DEEP_DIVE.md.2026-09-15.bak c:\Users\bot6\docs\01_ARCHITECTURE_DEEP_DIVE.md -Force` |
| 3 | alternativ via Git | `git -C c:\Users\bot6 checkout HEAD -- funktionen.md docs/01_ARCHITECTURE_DEEP_DIVE.md` |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | `scripts/tmp_doku_konsolidierung_20260915.py` (Splice + 9 Anchor-Assertions) | ALLES OK (158749→128076 Zeichen) | 2026-09-16 |
| 2 | DoD-Checks: genau 1 multimodal-H2; Stale-API-Greps (lediglich legitime Historik-/Fremdklassen-Treffer); Zeilenzahlen vs. Code (706/709/685/580/1650); API-Angaben Sektion 22 vs. `langgraph_real.py` (L354/361/362) | alle OK | 2026-09-16 |