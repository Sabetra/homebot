# Workdoc: Finance SOTA Phase 1 – Monarch-Core (Forecasting)

> **Erstellt:** 2026-09-12 18:20
> **Status:** FERTIG (2026-09-12, Phase 1 umgesetzt, 220/220 Tests grün)
> **Autor:** Cline-Agent

---

## Original-Auftrag

> Die Analysefunktionen im Finance-Tab sind nicht auf SOTA-Niveau. Ich müsste
> z.B. auch Forecasts etc. machen können. Recherchiere im Internet und wende
> eine CoT und ToT an. Frage beides kritisch, und optimiere soweit sinnvoll.
> Gehe bei der Antwort genauso vor. Bewerte Varianten und Optionen in 5
> Kategorien mit 1-7 Sternen, um Empfehlungen abzugeben.

> **Task-Resumption (2026-09-12):** Analyse + 5-Kategorien-Bewertung +
> Roadmap-Vorentscheidung (Phase 1/2/3) in der Vor-Session geliefert.
> Jetzt: Implementierung von Phase 1 „Monarch-Core".

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| `finance_upcoming_bills` (Bills-Kalender, deterministisch) | Phase 2: Sinking Funds / Goals (DB-Design offen) |
| `finance_cash_flow_forecast` (Schedule-first-Hybrid, CI, Balance-Kurve) | Phase 3: Szenario-What-If-Engine, ML-Forecasting |
| `finance_subscription_audit` (Abo-Audit auf Basis Recurring-Detection) | LLM-generierte Forecasts, externe APIs, Cloud |
| UI: neuer Sub-Tab „📈 Prognosen" (Balance-Band, Bills-Tabelle, Abo-Audit) | Abonnement-Kündigungsservice, Cashback |
| i18n DE/EN/BG, Tests, Doku (03_FINANCE_MODULE, funktionen.md) | Änderungen an `expense_forecast` (Backward-Compat) |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Upcoming-Bills: korrekte Next-Due-Dates, Fenster-Filter, deterministisch | pytest (neue Suite) | ☑ 2026-09-12 |
| 2 | Cash-Flow-Forecast: saisonaler Residual-Anteil, CI-Reihenfolge, Balance-Kette, kein Future-Leakage, Transfer-Ausschluss, Multi-Currency | pytest (neue Suite) | ☑ 2026-09-12 |
| 3 | Subscription-Audit: Klassifikation + Monats-/Jahres-Totals, Multi-Currency | pytest (neue Suite) | ☑ 2026-09-12 |
| 4 | Tools registriert: Toolkit-Dispatch, Schemas, Profile, Chat-Routing, Reflector, Planner (alle Fail-Fast-Validatoren grün) | pytest existing + Modul-Import | ☑ 2026-09-12 |
| 5 | UI: „📈 Prognosen"-Tab rendert (Plotly-Balance-Band, Bills, Audit) | Code-Review + py_compile | ☑ 2026-09-12 |
| 6 | i18n DE/EN/BG vollständig, JSON valide | json.load + Doku-Check | ☑ 2026-09-12 |
| 7 | Finance-Testsuite + Planner-Tests grün | run_pytest_venv.ps1 | ☑ 2026-09-12 (220/220) |
| 8 | Doku: 03_FINANCE_MODULE.md + funktionen.md aktualisiert | Review | ☑ 2026-09-12 |
## Alternativen & Entscheidung (5 Kategorien, 1–7)

Kategorien = Standard-Prompt Schritt 11: Korrektheit, Robustheit, Wartbarkeit,
Performance, Migrationsrisiko (7 = beste Punktzahl).

| # | Option | Korrektheit | Robustheit | Wartbarkeit | Performance | Migrationsrisiko | Entscheidung |
|---|--------|-------------|------------|-------------|-------------|------------------|--------------|
| A | **Schedule-first-Hybrid** (Recurring-Anchor + saisonaler variabler Residual + Bootstrap-CI + Balance-Kurve, rein deterministisch) | 7 (Verifizierbar: Balance-Kette, CI-Reihenfolge, Seasonality-Tests) | 7 (keine neuen Deps, Degradation bei <12 Monaten, Transfer-Ausschluss via bestehende Klausel) | 7 (reine Python-Stdlib, Finanz-Tools-Pattern, fail-fast) | 7 (O(n) über Buchungen; B=1000 Bootstrap trivial) | 6 (neue Tools, keine Änderungen an bestehenden) | **GEWÄHLT** |
| B | ML-Forecast (Prophet/ARIMA/XGBoost) | 4 (12–36 Monatspunkte: Overfit-Risiko) | 3 (neue schwere Deps, Non-Determinismus, AGPL-Lizenzcheck) | 3 (Trainings-Pipeline, Versionsdrift) | 3 (Train-Inferenz-Overhead pro Query) | 3 (venv-Breakage, Rollback) | Abgelehnt (Phase-3-Experiment offen) |
| C | LLM als Forecast-Engine | 3 (Arithmetik unzuverlässig, nicht reproduzierbar) | 3 (Token-Kosten, Halluzination, keine Testbarkeit) | 3 (Prompt-Drift, keine Unit-Tests) | 2 (LLM-Runde pro Forecast) | 2 (verletzt „deterministische Finanz-Tools"-Konvention) | Abgelehnt (LLM nur als Erläuterung im Chat) |
| D | Nur Statistik-Upgrade der `expense_forecast` (Saison + Trend, ohne Bills/Balance) | 5 (besser als Rolling-Mean, aber keine Bills/Einkommen/Balance) | 5 | 6 | 6 | 7 | Teilweise in A enthalten (Methode wiederverwendet) |
| E | Voll-Feature-Suite (Kündigungsservice, Sparziele, Cashback) | 5 | 2 (externe Abhängigkeiten, Rechts-/Provisions-Fläche) | 2 | 4 | 2 | Abgelehnt (Phase 1-Nicht-Scope; Phase 2 = Goals/Sinking) |

> **Auswahl:** A — Begründung: SOTA-Konsens privater Haushalts-Tools
> (Cash Predict: „current balances + scheduled income + upcoming bills";
> PocketSmith: deterministische Szenario-Projektion; Finanzguru: Abo-Erkennung
> + Prognosen als Kern-Feature) ist Schedule-first + variable Residual-
> Schätzung. Bei 12–36 Monatspunkten pro Haushalt schlägt deterministische
> Dekomposition (Trend × Saison + Residual-CI) ML in Robustheit/Wartbarkeit,
> bei vollem Nutzen. Beleg: Code (`_recurring_groups`, `_monthly_expenses`)
> + Recherche 2026-09-12.

## Verifizierte Fakten

| # | Fakt | Beleg (Datei:Zeile / Symbol) |
|---|------|------------------------------|
| 1 | Facts: `booking_date`, `month`, `category`, `counterparty`; Transfers per Default ausgeschlossen | `finance/db_schema.py::list_analysis_facts`, `_non_transfer_clause` |
| 2 | `balance_at(account_id, as_of_date)` liefert kum. Saldo (Opening-Balance + Summe) | `finance/db_schema.py::balance_at` |
| 3 | Chat-Routing hat Fail-Fast-Validatoren (Prompt-Coverage, Reflector-Dispatch) | `finance/chat.py::_validate_*_coverage` |
| 4 | Reflector-Actions = Single Source of Truth + Literal-Union | `finance/query_reflector.py::_REFLECTOR_ACTIONS` (L31–64) |
| 5 | Tool-Name `finance_X` ↔ Methode `X` in FinanceTools (Canary-Dispatch) | `scripts/run_finance_canary.py` |
| 6 | UI nutzt `_tr("finance_ui.*")` + Plotly; Tabs via `st.tabs([...])` | `finance/tab.py::render_finance_tab` |
| 7 | i18n `finance_ui` hat `tabs` + Sektionen; DE/EN/BG | `i18n/locales/de.json` |
| 8 | SOTA-Recherche: Cash Predict (Balance+Income+Bills), PocketSmith (What-If), Finanzguru (Abo-Erkennung+Prognosen), Copilot-Schwäche (keine unbezahlten Recurrings) | Web 2026-09-12 |

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderung | Status |
|---|--------|--------------------|------------|-----------|--------|
| 1 | Chat-Validatoren brechen (Drift Prompt/Reflector/Dispatch) | mittel | hoch (Modul-Load fail-fast) | Alle Listen synchron pflegen; Validatoren laufen bei jedem Import | offen |
| 2 | Saison-Indizes bei <12 Monaten Daten instabil | hoch | mittel | Degradation: <12 Monate → keine Seasonalität (Index=1), dokumentiert | offen |
| 3 | CI bei kleinen Stichproben instabil | mittel | niedrig | Residual-Bootstrap mit fester Seed; `confidence_level`-Param; Doku-Hinweis | offen |
| 4 | Balance-Kurve bei Multi-Currency unsinnig | mittel | mittel | Nur Single-Currency/Single-Konto → Balance, sonst null (bestehendes `estimated_savings`-Pattern) | offen |
| 5 | UI-Performance bei großen DBs | niedrig | niedrig | Fakten-Liste limitiert; Bootstrap O(B·n), n=Monate | offen |

## Sicherheits- & PII-Implikationen

| # | Aspekt | Implikation | Gegenmaßnahme |
|---|--------|-------------|---------------|
| 1 | Finance-Daten bleiben lokal | keine neuen Netzwerkpfade | nur lokale DB-Reads, keine neuen Deps |
| 2 | Counterparty-Namen in UI/Chat | PII bleibt im User-Tab | bestehende `pii_protection`-Schicht unangetastet |

## Rollback-Strategie

| Schritt | Aktion | Befehl / Referenz |
|---------|--------|-------------------|
| 1 | Backup-Stand wiederherstellen | `Copy-Item ~\homebot_backups\finance_sota_phase1_20260912\<datei> <repo>\<datei>` |
| 2 | Neue Test-Datei löschen | `Remove-Item tests\test_finance_forecast_tools.py` |
| 3 | Autosave-Watcher als Zweitquelle | `git log --oneline -- <datei>` |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `finance/tools.py` | +3 Tools (upcoming_bills, cash_flow_forecast, subscription_audit) + Helpers | ✅ 2026-09-12 |
| 2 | `agent_toolkit.py` | +3 Dispatch-Einträge + Wrapper | ✅ 2026-09-12 |
| 3 | `agent/tool_schemas.py` | +3 Schemas | ✅ 2026-09-12 |
| 4 | `agent/tool_profiles.py` | FINANCE_ANALYTICS +3 | ✅ 2026-09-12 |
| 5 | `finance/chat.py` | Prompt +3 Routings, +3 Retry-Dispatch | ✅ 2026-09-12 |
| 6 | `finance/query_reflector.py` | +3 Actions (Tuple + Literal) | ✅ 2026-09-12 |
| 7 | `finance/grammar_compiler.py` | FINANCE_TOOL_NAMES + REFLECTOR_ACTIONS +3 | ✅ 2026-09-12 |
| 8 | `finance/query_planner.py` | Planner-Prompt +3 Routings | ✅ 2026-09-12 |
| 9 | `finance/tab.py` | neuer Sub-Tab „📈 Prognosen" (`_render_forecast_tab`, Plotly) | ✅ 2026-09-12 |
| 10 | `i18n/locales/*.json` | +`finance_ui.forecast.*` (43 Keys) + Tab | ✅ 2026-09-12 |
| 11 | `tests/test_finance_monarch_core.py` | neue Suite (26 Tests) | ✅ 2026-09-12 (26/26 PASS) |
| 12 | `docs/03_FINANCE_MODULE.md`, `funktionen.md` | Doku-Updates | ✅ 2026-09-12 |

