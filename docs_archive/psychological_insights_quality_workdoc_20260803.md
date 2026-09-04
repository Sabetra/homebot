---
p1: true
created: 2026-08-03
last_updated: 2026-08-03
---
# Workdoc: Psych-Modul Insight-Qualität

## Ursprünglicher Auftrag

Umsetzung der korrigierten Analyse für psychologische Insights:
- P0: Provider-Fehler propagieren + Tests
- P1: Persistente Nutzerkorrektur mit Audit-Status
- P1: Deterministische hybride Top-8-Auswahl
- P1/P2: Typabhängige Aktualität und Supersession
- P2: Tokenbudget end-to-end erzwingen
- P2: Semantische Paraphrasen-Deduplikation

## Scope

| Included | Excluded |
|----------|----------|
| UserInsightsProvider Fehler-Propagation | Cross-Encoder Reranking |
| DB-Migration: correction_status | Contextual Bandits / Plackett-Luce |
| Hybride Top-8-Auswahl | Neue ML-Modelle |
| Typ-abhängige Gültigkeit | Azure/Cloud-Deployments |
| Tokenbudget-Überlauf-Reparatur | Generalistische Azure-Changes |

## Definition of Done

- [x] P0: UserInsightsProvider propagiert Exceptions (nicht silent `[]`)
- [x] P0: Tests für Provider-Fehler-Propagation passend (5/5 PASS)
- [ ] P1: DB-Schema hat `correction_status`, `corrected_at`, `corrected_by`, `correction_reason`
- [ ] P1: Retrieval filtert `rejected`/`superseded` Insights
- [ ] P1: Hybride Top-8-Auswahl implementiert
- [ ] P1: Tests für alle drei P1-Maßnahmen
- [ ] P1/P2: Typ-abhängige Halbwertszeiten implementiert
- [ ] P2: Tokenbudget validiert nach Emergency-Trim
- [ ] Alle bestehenden Tests weiterhin passend

## Verifizierte Fakten

| Fakt | Quelle |
|------|--------|
| TokenBudgetManager aktiv | `agent_chatbot_logic.py` ~3125, ~3630 |
| UserInsightsProvider verschluckt Exceptions | `user_context_builder/providers/user_insights.py` |
| KG-Provider propagiert korrekt | `user_context_builder/providers/knowledge_graph.py` |
| DB hat `validated_at`, kein Korrektur-Status | `psychological_user_insight_extractor.py` 163-178 |
| Max 8 Insights injiziert | `user_context_builder/builder.py` |

## Risiko & Impact

| Risiko | Wahrscheinlichkeit | Auswirkung | Minderung |
|--------|-------------------|------------|-----------|
| DB-Migration bricht bestehende Daten | Niedrig | Hoch | Idempotent, Backfill |
| Tokenbudget-Trim entfernt falsche Inhalte | Mittel | Mittel | Tests, Fallback |
| Hybride Auswahl zu starr | Niedrig | Niedrig | Konfigurierbar |

## Rollback-Strategie

- Alle DB-Änderungen idempotent via Migrationen
- Code-Backups in `backups/`
- Feature-Toggle für hybride Auswahl (Konstante `HYBRID_SELECTION_ENABLED`)

## Änderungen & Testergebnisse

| Datei | Änderung | Test |
|-------|----------|------|
| `user_context_builder/providers/user_insights.py` | Exception-Propagation (raise statt silent `[]`) | `test_user_insights_provider_error_propagation.py` (5/5 PASS) |
| `tests/test_user_insights_provider_error_propagation.py` | Neue Testsuite: Propagation, Scoring, Builder-Error-Capture | 5/5 PASS |
