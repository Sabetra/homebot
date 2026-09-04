# mypy Full-Scope Cleanup Workdoc

## Auftrag
Alle aktiven Module des Projektes per mypy untersuchen, Fehler clustern und sequentiell beheben.
Aktueller Stand: 360 Fehler in 98 Dateien (von 496 reduziert, 27.4%).

## Scope
- mypy-Fehler in aktiven Python-Dateien beheben (gemäß mypy.ini scope)
- Kleinstmögliche, konsistente Änderungen
- GPU-/LLM-Parameter nicht verändern (gemäß AGENTS.md § GPU-/LLM-Parameter)
- Vor Datei-Überarbeitungen Backup anlegen (gemäß AGENTS.md § Arbeitsweise)

## Nicht-Scope
- Deadcode-Entfernung
- Architektur-Refactoring
- Dokumentations-Erweiterungen (nur bei Vertragsänderung)

## AGENTS.md-Konformität
- ✅ Code ist Source of Truth (§ Konventionen)
- ✅ Backup vor Änderungen (§ Arbeitsweise)
- ✅ GPU-Parameter nicht erhöhen (§ GPU-/LLM-Parameter)
- ✅ Tests nach Änderungen ausführen (§ Arbeitsweise)
- ✅ Root-Cause-Fixes, keine silent-fallbacks (§ Konventionen)

## Verifizierte Fakten (aus mypy_live_scope_errors.txt)

### Error-Code-Verteilung (360 Fehler)
| Code | Anzahl | Dateien | Hebelwirkung |
|------|--------|---------|-------------|
| no-any-return | ~60 | 43 | Hoch - Upstream-Quellen typisieren |
| assignment | ~35 | 20 | Mittel - viele lazy-init Patterns |
| attr-defined | ~25 | 12 | Mittel - dynamische Attributes |
| operator | ~20 | 8 | Mittel - Typ-Inkompatibilitaeten |
| index | ~15 | 6 | Mittel - Dictionary/Sequenz-Typen |
| var-annotated | ~15 | 10 | Niedrig - Type-Annotations hinzufügen |
| misc | ~15 | 5 | Niedrig - Pro-Fall |
| no-untyped-def | ~9 | 7 | Niedrig - Signaturen ergaenzen |
| no-redef | ~9 | 4 | Niedrig - Variable-Umbenennung |
| union-attr | ~10 | 5 | Niedrig - None-Checks |
| call-overload | ~5 | 1 | Niedrig - Argument-Typen |
| dict-item | ~4 | 3 | Niedrig - Dict-Wert-Typen |
| return-value | ~4 | 3 | Niedrig - Return-Typen |
| call-arg | ~3 | 2 | Niedrig - Argument-Typen |
| name-defined | ~1 | 1 | Sehr niedrig |
| valid-type | ~1 | 1 | Sehr niedrig |
| unused-ignore | ~1 | 1 | Sehr niedrig |
| truthy-function | ~1 | 1 | Sehr niedrig |
| annotation-unchecked | ~15 (notes) | - | Notes, keine Errors |

### Top-10 Problem-Dateien
1. agent_toolkit.py: ~35 Fehler (meist no-any-return)
2. ui_tabs/chat_tab.py: ~22 Fehler
3. agent/multimodal_integration.py: ~22 Fehler
4. agent/orchestrator.py: ~16 Fehler
5. utils/error_handling.py: ~15 Fehler
6. utils/intelligent_workspace_cleanup.py: ~14 Fehler
7. kg_dashboard.py: ~11 Fehler
8. psychological_session/workflow/langgraph_real.py: ~11 Fehler
9. agent/react_agent.py: ~11 Fehler
10. utils/live_chunk_monitor.py: ~8 Fehler

## Strategie

### Phase A: Dateien mit hohem "object"-Typ-Problem
Diese Dateien haben variable Typen als "object" inferiert, was Kaskaden von attr-defined/operator/index Fehlern ausloest:
- utils/intelligent_workspace_cleanup.py (14 Fehler)
- utils/live_chunk_monitor.py (8 Fehler)
- agent/orchestrator.py Zeilen 4134-4779 (~8 Fehler)
- agent/multimodal_integration.py (~12 Fehler)

### Phase B: Lazy-Init Patterns (assignment: None -> non-None)
Systematisches Pattern: Module-level Variables initialisiert mit None, spaeter mit Instanz befuellt.
Loesung: Optional[T] oder TYPE_CHECKING Guard.
Betroffen: ~20 Dateien, ~30 Fehler

### Phase C: no-any-return (restliche ~50 Fehler)
Pro-Datei entscheiden: cast(str), explizite Return-Typen, oder # type: ignore mit Begründung.

### Phase D: Restliche Einzel-Fehler
Pro-Fall Behandlung.

## Hypothesen
- H1: Die "object"-Typ-Kaskaden lassen sich durch 1-2 Type-Annotations pro Datei brechen
- H2: Lazy-Init Patterns lassen sich einheitlich mit Optional[T] loesen
- H3: no-any-return ist oft ein Symptom, nicht die Ursache

## Aenderungen & Testergebnisse
(Dynamisch aktualisiert)