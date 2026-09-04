# mypy Hardening Workdoc — 2026-07-28

## Auftrag
Alle aktiven Module des Projektes per mypy untersuchen, Fehler clustern und einen sequenziellen Behebeplan erstellen.

## Scope
- **In Scope:** mypy-Scope-Erweiterung auf alle aktiven Module, Fehler-Cluster-Analyse, sequenzieller Behebeplan
- **Nicht in Scope:** Tatsächliches Beheben der Typfehler (folgt in separaten Tasks), Änderungen am Test-Setup

## Verifizierte Fakten

### Aktuelle mypy-Konfiguration
- Datei: `mypy.ini` (100 Zeilen)
- Original `files=`: `psychological_session_interface.py, agent/, emotional_analyzer.py, psychological_support/`
- Erweitert auf: `*.py, agent/, chatbot_logic.py, agent_chatbot_logic.py, database/, utils/, i18n/, llm_utils/, pii_protection/, user_context_builder/, psychological_session/, psychological_support/, finance/, ui_tabs/, content_extractor/, html_parser/, schemas/, psychological_session_interface.py, emotional_analyzer.py, kg_dashboard.py, models_pydantic_v2.py, code_executor_engine.py, structured_data_extractor.py, chat_context_manager.py, chat_image_generator.py, generic_visualization_tool.py, gpu_optimizer.py, pdf_readability_checker.py, agent_toolkit.py, enhanced_streamlit_bot.py, llm_structured_wrapper.py`
- Python-Version: 3.11
- Strictness: Medium (warn_return_any=True, disallow_untyped_defs=False global)
- `scripts/` und `tests/` ausgeschlossen (keine produktiven Module)

### Neue Voll-Baseline (erweiterter Scope)
- Quelle: `mypy_full_scope_raw.txt` (UTF-16 LE, 142.766 Bytes)
- **519 Fehler, 61 Notes, 115 betroffene Dateien**

### Cluster-Statistik (vollständiger Scope, 519 Fehler)

| Cluster | Code | Anzahl | % | Dateien | Schwere |
|---------|------|--------|---|---------|---------|
| A: Any-Returns | no-any-return | 117 | 22.5% | 42 | Niedrig |
| B: Unreachable | unreachable | 69 | 13.3% | 29 | Mittel |
| C: Assignment | assignment | 63 | 12.1% | 29 | Mittel |
| D: Arg-Type | arg-type | 40 | 7.7% | 20 | **Hoch** |
| E: Attr-Defined | attr-defined | 34 | 6.5% | 10 | **Hoch** |
| F: Has-Type | has-type | 30 | 5.8% | 2 | Mittel |
| G: Index | index | 21 | 4.0% | 8 | Mittel |
| H: Var-Annotated | var-annotated | 19 | 3.7% | 12 | Niedrig |
| I: Operator | operator | 19 | 3.7% | 7 | Mittel |
| J: Annotation-Unchecked | annotation-unchecked | 19 | 3.7% | 9 | Niedrig |
| K: Misc | misc | 19 | 3.7% | 10 | Variabel |
| L: Union-Attr | union-attr | 16 | 3.1% | 8 | **Hoch** |
| M: Unused-Ignore | unused-ignore | 11 | 2.1% | 4 | Niedrig |
| N: No-Redef | no-redef | 9 | 1.7% | 4 | Mittel |
| O: No-Untyped | no-untyped-def | 9 | 1.7% | 9 | Niedrig |
| P: Sonstige | dict-item, call-overload, return-value, etc. | 14 | 2.7% | - | Variabel |

## Sequenzieller Behebeplan

### Phase 1 — Kritische Runtime-Risiken (Cluster D, E, L = 94 Fehler, 17.3%)
- **Ziel:** arg-type, attr-defined, union-attr
- **Risiko:** NoneAccess, falsche Signaturen, Runtime-Crashes
- **Schätzung:** ~8-12 Dateien mit höchster Dichte
- **Strategie:** Datei-für-Datei, Signatur-Anpassungen, None-Checks

### Phase 2 — Assignment & Operator (Cluster C, G, I = 103 Fehler, 19.8%)
- **Ziel:** assignment, index, operator
- **Risiko:** Typ-Inkonsistenzen bei Zuweisungen
- **Strategie:** Lazy-Init Pattern mit `| None`, Index-Typen korrigieren

### Phase 3 — Unreachable Code (Cluster B = 69 Fehler, 13.3%)
- **Ziel:** unreachable
- **Risiko:** Dead code nach Refactoring
- **Strategie:** Dead-Code-Identifikation, frühe Returns von echten Lücken trennen

### Phase 4 — Any-Returns & Annotations (Cluster A, H, J = 146 Fehler, 28.1%)
- **Ziel:** no-any-return, var-annotated, annotation-unchecked
- **Risiko:** Niedrig, aber Strict-Mode-Blocker
- **Strategie:** Explizite Return-Typen, `# type: ignore` wo extern

### Phase 5 — Aufräumen (Cluster F, M, N, O = 59 Fehler, 11.4%)
- **Ziel:** has-type, unused-ignore, no-redef, no-untyped-def
- **Risiko:** Mittel
- **Strategie:** Invalid-Annotations entfernen, alte Igares aufräumen

### Phase 6 — Strict-Mode-Härtung
- `disallow_untyped_defs = True` global
- `check_untyped_defs = True` global
- Neue Baseline, Restfehler dokumentieren

## Änderungen & Testergebnisse

| Datum | Änderung | Ergebnis |
|-------|----------|----------|
| 2026-07-28 20:33 | mypy.ini backup | `backups/mypy.ini.bak_20260728` |
| 2026-07-28 20:34 | Scope erweitert, Line-Continuation fix | mypy Läuft, 519 Fehler, 115 Dateien |
| 2026-07-28 20:39 | Cluster-Analyse mit Python-Parser | Verteilung siehe Tabelle oben |

## Offene Hypothesen
- finance/ und web_search/ haben eigene Cluster-Patterns (noch nicht isoliert)
- tests/ Ausschluss ist korrekt (nicht produktiver Code)
- ~30% der Fehler sind Strict-Mode-Blocker aber kein Runtime-Risiko