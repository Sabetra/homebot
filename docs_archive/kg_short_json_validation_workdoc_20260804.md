# Workdoc: Kurze valide KG-JSON-Antworten

> **Erstellt:** 2026-08-04
> **Abschluss-Ziel:** 2026-08-04
> **Status:** ABGESCHLOSSEN
> **Autor:** GitHub Copilot

## Original-Auftrag

WARNING:llm_utils.guaranteed_caller:⚠️ Response validation failed (attempt 3): length=31
ERROR:llm_utils.guaranteed_caller:❌ Alle 3 LLM-Call Attempts fehlgeschlagen!
ERROR:llm_utils.robust_response_handler:❌ Alle Parsing-Methoden fehlgeschlagen für Response: {
    "error": "LLM call failed after 3 attempts",
    "query": "Du bist ein präziser Knowledge Graph Extractor für therapeutische Gespräche. 

WICHTIG: Antworte AUS...",
    "fallback": true,
    "su...
WARNING:agent.llm_knowledge_graph_enhanced:⚠️ KG Response parsing used fallback: fallback

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| KG-spezifische Response-Validierung | Modell-/GPU-Tuning |
| Fehlerstatus vor Parsing auswerten | Neuaufbau der primären KG-Pipeline |
| Caller-/Extractor-Vertragstests | Änderung der Intent-/Privacy-Validierung |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Valides 31-Zeichen-KG-JSON wird beim ersten Versuch akzeptiert | Unit-Test | ✅ |
| 2 | Malformes/strukturell falsches KG-JSON wird weiter retried | Unit-Test | ✅ |
| 3 | `success=False`-Envelope wird nicht an den KG-Parser übergeben | Extractor-Test | ✅ |
| 4 | Bestehende Caller/KG/Psych-Tests bleiben grün | Pytest | ✅ |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Korrektheit | Robustheit | Performance | Risiko | Entscheidung |
|---|--------|-----|--------|-------------|------------|-------------|--------|--------------|
| A | KG-Mindestlänge auf 10 senken | Klein | Malformes Langtext gilt weiter als valide | 3 | 2 | 7 | 4 | Verworfen |
| B | Mindestlänge global entfernen | Keine False Negatives | Schwächt Intent/Privacy | 2 | 2 | 7 | 2 | Verworfen |
| C | Optionaler Validator pro Call | Domänenvertrag, abwärtskompatibel | Kleine API-Erweiterung | 7 | 7 | 7 | 6 | Umsetzen |

> **Auswahl:** Option C. Struktur ist für JSON-Extraktion das korrekte Validitätskriterium; Länge bleibt Default für freie Textantworten.

## Abhängigkeiten & Stakeholder

| # | Abhängigkeit | Art | Impact | Status |
|---|--------------|-----|--------|--------|
| 1 | `GuaranteedLLMCaller` | Shared Utility | optionale API-Erweiterung | abgeschlossen |
| 2 | `EnhancedLLMKnowledgeGraphExtractor` | Produktiver Psych-KG-Pfad | nutzt Strukturvalidator | abgeschlossen |
| 3 | Intent-/Privacy-Handler | andere Caller-Nutzer | unveränderter Default | verifiziert |

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|-------|
| 1 | KG konfiguriert pauschal `min_response_length=50` | `EnhancedLLMKnowledgeGraphExtractor.__init__` |
| 2 | `{"triples": [], "metadata": {}}` hat 31 Zeichen und ist valides KG-JSON | deterministische Längen-/JSON-Prüfung |
| 3 | Nach Retry-Fehler wird ein `{"error": ..., "fallback": true}`-Envelope erzeugt | `GuaranteedLLMCaller.call_with_guarantee()` |
| 4 | Der KG-Extractor ignoriert `LLMCallResult.success` und parst den Envelope | `extract_knowledge_graph()` |
| 5 | Enhanced-Extractor wird von `psychological_support/psychological_db.py` importiert | aktive Referenz |

## Hypothese und Falsifizierung

**Hypothese:** Die 31-Zeichen-Antwort ist valides leeres KG-JSON und wird ausschließlich wegen der pauschalen 50-Zeichen-Grenze verworfen; dadurch entsteht erst die Fehler-Envelope-Parsing-Kaskade.

**Falsifizierung:** Ein Test mit exakt dieser Antwort muss vor dem Fix drei Calls/Fallback zeigen und nach strukturierter Validierung einen erfolgreichen Call bei Versuch 1. Ein künstliches `success=False` darf den Parser nicht erreichen.

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmaßnahme | Status |
|---|--------|-------------------|------------|-------------------|--------|
| 1 | Shared Caller regressiert | niedrig | hoch | optionaler Validator, Defaults unverändert | geschlossen |
| 2 | JSON-Codeblöcke werden fälschlich verworfen | mittel | mittel | direkte und fenced JSON-Verträge getestet | geschlossen |
| 3 | Echter LLM-Ausfall wird still | niedrig | mittel | explizites Warning mit Error/Attempts, lokaler Fallback | geschlossen |

## Sicherheits- & PII-Implikationen

Keine neuen Datenflüsse. Fehler-Envelopes mit Promptausschnitten werden nicht mehr als KG-Inhalt weiterverarbeitet.

## Rollback-Strategie

Gezielte Backups unter `backups/kg_short_json_validation_20260804/`.

## Änderungen und Testergebnisse

- Reproduktion vor Fix: `2 failed`; 31-Zeichen-JSON verursachte drei Calls, Fehler-Envelope erreichte den Parser.
- `GuaranteedLLMCaller.call_with_guarantee()` um optionalen, rückwärtskompatiblen `response_validator` ergänzt.
- KG-Validator akzeptiert direktes oder fenced JSON nur mit einer `triples`-Liste; Zeichenlänge ist irrelevant.
- `LLMCallResult.success=False` wird vor Parsing geprüft und pro Chunk explizit in `_fallback_extraction()` geroutet.
- Retry-Logs unterscheiden `mode=domain` und `mode=min_length:N`.
- Fokussierter Caller-/KG-Vertrag: `5 passed`.
- Breite Psych-/KG-Regression: `89 passed`.
- Editor-Diagnostik: keine Fehler in Caller, Extractor oder Test.
