# Workdoc: Quarantäne-Regeneration auf SOTA-Stand (DLQ-Pattern)

> **Erstellt:** 2026-08-12
> **Status:** ABGESCHLOSSEN
> **Autor:** Bot

## Original-Auftrag
Die im kritischen Review identifizierten SOTA-Lücken der Triple-Regeneration aus Quarantäne beheben; Internet-Recherche falls sinnvoll.

## Recherche
DuckDuckGo (2026-08-12): DLQ-Best-Practices (systemoverflow.com, codelit.io, conduktor.io) — Konsens:
transient vs. permanent klassifizieren, Poison-Messages terminal markieren statt endlos retryen,
Replay/Redrive als expliziter Schritt mit Reset, state-aware Retention. Konsequenz: Taxonomie
`pending/failed/completed/permanent_failed` + Reopen-mit-Reset + Purge nur terminal.

## Änderungen ([agent/rag_store/core/quality.py](../agent/rag_store/core/quality.py))

| # | Lücke | Fix |
|---|-------|-----|
| 1 | `failed` vermischte transient/permanent | Neuer Terminalstatus `permanent_failed`: Invalid-Backup sofort; transiente Fehler eskalieren bei `attempts >= max_retry_attempts` (in `_mark_quarantine_state`) |
| 2 | Reopen nur für `completed` | `reopen_aged_quarantine` reopent `completed` + `permanent_failed`, setzt `regeneration_attempts=0` |
| 3 | Binärer Anti-Loop machte regenerierte Triples unheilbar | Lineage-Zähler `regeneration_generation` (Param `max_regeneration_generations=2`); Legacy-Flag wird als Gen 1 gelesen |
| 4 | Purge löschte auch unbearbeitete Evidenz | `purge_expired_quarantine` löscht nur `completed`/`permanent_failed` |
| 5 | Ein einzelner Reranker als Richter | Dual-Gate: `_lexical_grounding_ok` (Subject+Object-Token im Chunk) zusätzlich zum Score; Scores ≥0.7 passieren allein |
| 6 | Batch-Starvation durch tote Einträge | `ORDER BY` pending-first; permanent_failed verlässt die Auswahlmenge dauerhaft |
| 7 | O(n)-Duplikat-Scan | Expression-Index `idx_quarantine_triple_hash` (mit `json_valid`-Guard für Legacy-Backups) |
| 8 | Keine Selbst-Beobachtung | `grounding_score_summary` (count/min/mean/max) + `quarantine_marked_permanent_failed` in Stats |
| 9 | Kein KG-Sync-Hinweis | `kg_reload_recommended`-Flag; Dashboard zeigt Reload-Warnung |

Weitere Dateien: [refactored_gui/quality_dashboard.py](../refactored_gui/quality_dashboard.py) (neue Felder sichtbar),
[tests/test_quality_regeneration.py](../tests/test_quality_regeneration.py) (+5 Tests),
[scripts/run_pytest_venv.ps1](../scripts/run_pytest_venv.ps1) (Fix: `$args` statt Parameter — `-p` kollidierte mit `-PytestArgs`).

## Nicht geändert (bewusst)
- `drain_quarantine_regeneration.py`: Backlog-Query (pending/failed<limit) bleibt korrekt — permanent_failed fällt automatisch heraus.
- Kein zusätzliches NLI-Modell (VRAM-Budget; vgl. P2-X-Entscheidung in 00_CONTEXT_MASTER).

## Testergebnisse
| Befehl | Ergebnis |
|--------|----------|
| `run_pytest_venv.ps1 tests/test_quality_regeneration.py tests/test_quarantine_regeneration_drain.py tests/test_quality_dashboard_fast.py tests/test_quality_dashboard_regeneration_integration.py` | 24 passed in 14.60s |

## Backups
`%USERPROFILE%ot6_backups\quarantine_sota_20260812\`
