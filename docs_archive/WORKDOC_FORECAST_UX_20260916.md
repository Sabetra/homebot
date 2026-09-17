# Workdoc: Forecast UX (Qwen-Implementierung 2026-09-16)

> **Erstellt:** 2026-09-16 (Session 2)
> **Abschluss-Ziel:** 2026-09-16 (Session 3)
> **Status:** IN_ARBEIT
> **Autor:** Cline Agent (Fortsetzung der Qwen-Session)
> **Reviewer:** —

**Quell-Auftrag:** `docs_archive/FORECAST_UX_QWEN_IMPLEMENTATION_PROMPT_2026-09-16.md` (unverändert;
dort §4 = vollständiger Original-Auftrag, §5.3 = AP0-Definition).

---

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| F01–F05 (Goals-Overlay, Account-Isolation, UI-Reihenfolge, i18n-Keys) | Phase-1/Phase-2-Methodik ändern |
| D01–D04 (Ziel-Filter, 3-Zustands-Renderer, `include_goals=True`, UI-Strings) | Produktiv-DB / GPU / LLM-Live-Test |
| B01 (Zustands-Persistenz) | `effective_balance_at`-Semantik ändern |
| B02–B04 (Charts, Legenden, `forecast_months`) | Phase-1/2-Tool-Signaturen ändern (nur neue Option `include_goals`) |
| B05–B06 (Abo-Filter, `forecast_recurring_only`) | Budget-Modul |
| B07 (Tab-Klick) | Neue Dependencies |
| AP0–AP2, 24 Tests, 200 Finance-Tests, i18n-Parität | Release-Gate |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Goals-Overlay: Default AUS, `include_goals=True` reaktiv, Capped Draws, `net_with_goals`, `balance_with_goals` (Kette), `last_draw_month` | Tests (DoD#6, vgl. `test_finance_goals_schema.py`) | ☐ (besteht: DoD#6 Phase 2) |
| 2 | Account-Isolation: Goals nur für aktive IBAN (kein Leak), `iban`-Param optional | Tests: `test_goals_only_for_active_iban`, `test_goals_param_optional` | ☐ |
| 3 | UI: Ziel-Checkbox (Default AUS) vor Charts, Goals-Zeile, `goals_draw`/`net_with_goals`/`balance_with_goals` in Monats-Tabelle | `AppTest`-Render-Test + Key-Prüfung | ☐ |
| 4 | i18n DE/EN/BG: `forecast.goals.*` + `forecast.include_goals` (5+3=8 Keys), alle 3 Lokale | `i18n/i18n_manager.py`-Prüfung + Test | ☐ |
| 5 | 3-Zustands-Renderer (aktiv/fehlend/leer), `include_goals=True` nur bei aktivem Ziel | Renderer-Helfer-Test + AppTest | ☐ |
| 6 | Chart-Reihenfolge: Guthaben → Netto → optional Goals-Zeile; Legenden korrekt | AppTest / Helper-Test | ☐ |
| 7 | `forecast_months`-Slider (1–24, Default 12) wirkt sich auf Chart & Tabelle aus | AppTest / Helper-Test | ☐ |
| 8 | Abo-Filter (Default AUS), `forecast_recurring_only` (nur `recurring`, sonst 0) | Tools-Test (DoD#7) | ☐ |
| 9 | Tab-Klick = Rerun + Refresh (kein Stale-State) | AppTest: zweiter Klick ändert Ergebnis nach neuer Buchung | ☐ |
| 10 | ≥ 200 Finance-Tests grün (Bestand 220 + 24 neue = 244) | `run_pytest_venv.ps1 tests/ -q -k finance` | ☐ |
| 11 | i18n-Parität: alle 3 Lokale vollständig, kein Fallback | i18n-Test | ☐ |

## AP0 — Baseline (bestätigte Fakten)

| # | Fakt | Beleg |
|---|------|-------|
| 1 | `cash_flow_forecast` existiert mit `include_goals` (Default `False`), Goals-Overlay (`_goals_forecast_overlay`), Capped-Draws, `net_with_goals`, `balance_with_goals`, `last_draw_month` | `finance/tools.py:969`, `finance/tools.py:1025-1038` |
| 2 | `upcoming_bills` + `subscription_audit` existieren (Phase 1 Monarch-Core) | `finance/tools.py:1102-1267` |
| 3 | Forecast-Tab rendert 6 Sektionen; Goals-Overlay **wird nicht angefordert/angezeigt** (Prompt-Befund B) | `finance/tab.py:1898-2044` |
| 4 | `st.tabs(...)` ohne `key`/`on_change`; Tab-Klick = Streamlit-Rerun (kein expliziter Handler) | `finance/tab.py:1907` |
| 5 | `forecast_months`-Slider (1–24, Default 12) existiert; wirkt sich auf Tool-Call aus | `finance/tab.py:1924` |
| 6 | `_format_eur` = DE-Zahlenformat OHNE Währungssymbol (kein EUR-Bug) | `finance/tab.py:140-153` |
| 7 | `list_analysis_facts` liefert **kein** `account_id` (Prompt-Befund C0) | `finance/db_schema.py:2209-2221` |
| 8 | `balance_at` (Kontostand) vs. `effective_balance_at` (Legacy-Analyse) — Forecast nutzt `balance_at` | `finance/db_schema.py:2371-2455`, `finance/tools.py:1064` |
| 9 | `subscription_audit` nutzt `_recurring_groups` (min_occurrences=2), `is_fixed`-Flag, `subscription_like` | `finance/tools.py:1206-1249`, `finance/tools.py:2708-2739` |
| 10 | i18n: `finance_ui.forecast` = 43 Keys (Phase 1); `finance_ui.goals` existiert (Phase 2) | `i18n/locales/de.json:297-367` |
| 11 | Streamlit 1.60.0; `st.tabs` hat `key` + `on_change` (Default `'ignore'`) | Prompt §6 (venv-verifiziert) |
| 12 | Phase-2-Änderungen (2026-09-15) liegen als Working-Tree-Diff vor — **unverändert erhalten** | `git status --short` (12 M + 1 untracked) |

### AP0 — zu erhaltende externe Diffs (Working Tree, 2026-09-16)

```
M agent/tool_schemas.py
M docs/03_FINANCE_MODULE.md
M finance/db_schema.py
M finance/tab.py
M finance/tools.py
M funktionen.md
M i18n/locales/bg.json
M i18n/locales/de.json
M i18n/locales/en.json
M tests/test_finance_analytics_tools.py
M tests/test_finance_monarch_core.py
M tests/test_finance_tab_regressions.py
?? docs_archive/FORECAST_UX_QWEN_IMPLEMENTATION_PROMPT_2026-09-16.md
```

**Regel:** Vor jeder Etappe `git diff --stat` prüfen; keine dieser Dateien zurücksetzen.

### AP0 — Test-Baseline (vor Änderungen)

| Suite | Erwartung |
|-------|-----------|
| `tests/test_finance_monarch_core.py` (26 Tests) | PASS (Phase-1-Invarianten) |
| `tests/test_finance_goals_schema.py` (inkl. TestDoD6) | PASS (Goals-Overlay-Logik) |
| `tests/test_finance_tab_regressions.py` | PASS |
| `tests/test_finance_analytics_tools.py` | PASS |
| i18n-Suite | PASS (DE/EN/BG-Parität) |


## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | `_goals_forecast_overlay` funktioniert korrekt (DoD#6-Tests grün) | **bestätigt** (Phase-2-Tests existieren) | `run_pytest tests/test_finance_goals_schema.py -q` |
| 2 | `st.tabs(on_change=...)` funktioniert mit `key` in Streamlit 1.60.0 | offen | AppTest-Render mit `key`/`on_change` |
| 3 | `forecast_recurring_only` = `recurring`-Wert beibehalten, `variable`=0, `income`=0 | offen | Tools-Test |
| 4 | Goals-Overlay braucht `iban`-Param für Account-Isolation | offen | `test_goals_only_for_active_iban` |

## Risiko & Impact-Matrix

| # | Risiko | W. | Auswirkung | Minderung | Status |
|---|--------|----|------------|-----------|--------|
| 1 | Phase-1/2-Tests brechen durch neue Parameter | M | H | Additive Parameter (Default AUS); alle bestehenden Tests bleiben grün | offen |
| 2 | i18n-Paritätsbruch bei 3. Locale | M | M | Alle 3 Lokale gleichzeitig ergänzen; Paritäts-Test | offen |
| 3 | AppTest-Harness (Bare-Mode) instabil | M | M | Pure-Helper-Tests zuerst; AppTest nur für Render-Logik | offen |
| 4 | `st.tabs`-`on_change` nicht verfügbar (Version) | L | M | Streamlit 1.60.0 verifiziert; Fallback: `st.button` pro Tab | offen |

## Sicherheits- & PII-Implikationen

| # | Aspekt | Implikation | Gegenmaßnahme |
|---|--------|-------------|---------------|
| 1 | Finance-Daten | Nur lokale SQLite-Read-Queries; keine neuen Schreibpfade | Bestehende `FinanceDB`-API |
| 2 | Goals-Overlay | Keine PII; nur Cents-Integer + IBAN-Hash | Bestehende Validatoren |

## Rollback-Strategie

| Schritt | Aktion | Befehl |
|---------|--------|--------|
| 1 | `git checkout -- <datei>` für einzelne Datei | `git checkout -- finance/tools.py` |
| 2 | Workdoc + neue Tests löschen | `del docs/WORKDOC_FORECAST_UX_20260916.md` |
| 3 | Auto-Snapshot (10 min) als Fallback | `git log --oneline -- finance/tools.py` |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `docs/WORKDOC_FORECAST_UX_20260916.md` | AP0: Workdoc (Baseline, Diffs, DoD, Risiken) | — (Doku) |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | AP0: `git status --short` | 12 M + 1 ?? (Phase-2-Diff) | 2026-09-16 |
| 2 | AP0: Bestandsanalyse (tools.py, tab.py, db_schema.py, i18n) | bestätigt (Fakten 1–12) | 2026-09-16 |

