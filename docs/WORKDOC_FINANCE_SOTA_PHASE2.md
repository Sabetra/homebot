# Workdoc: Finance SOTA Phase 2 – Goals / Sinking Funds (DB-backed)

> **Erstellt:** 2026-09-12
> **Status:** IN_ARBEIT — Stand 2026-09-15: DoD#1–5, 7, 10 erfüllt (43/43 + 211/211 Tests reproduziert);
> offen: DoD#6 (Overlay-Stopp am Zielbetrag/-termin), DoD#8 (UI), DoD#9 (i18n),
> DoD#11 (00_CONTEXT_MASTER-Changelog + Archivierung)
> **Autor:** Cline-Agent (2026-09-12); Reparatur/Übernahme 2026-09-15 (GitHub Copilot + Cline)

---

## Original-Auftrag

> (Fortsetzung Finance SOTA Phase 1 „Monarch-Core", 2026-09-12, 220/220 Tests grün.)
> Nutzer-Entscheidung 2026-09-12: „Phase 2 jetzt planen: erst DB-Design-Entscheidung
> (neue SQLite-Tabellen `goals` + Zuordnung Buchungen↔Ziel) klären, dann Implementierung."

> Kontext Phase-1-Roadmap (Workdoc Phase 1, §Scope): „Phase 2: Sinking Funds /
> Goals (DB-Design offen)" — SOTA-Vorbilder: YNAB Sinking Funds (Ziel + fällige
> Periode + monatliche Rate), PocketSmith Savings Goals (Ziel + Datum +
> benötigte Monatsrate), Finanzguru/Moneymonk Sparziele (Ziel + Datum +
> Fortschritt).

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| DB-Design: Tabellen `goals` + `goal_contributions` (Buchung↔Ziel), Migrations-Integration (`_SCHEMA_STATEMENTS`) | Phase 3: Szenario-What-If-Engine, ML-Experimente |
| DAO: Goal-CRUD, Zuordnungen, Fortschritts-Queries (deterministisch, Cents-Integer) | Automatische Zuweisung von Buchungen an Ziele (nur read-only Vorschläge) |
| Tools: `finance_goals_overview`, `finance_sinking_fund_candidates` (read) + `finance_goal_create`, `finance_goal_assign_tx`, `finance_goal_unassign_tx` (write) | Mehrere Ziele pro Buchung (M2M) — v1: exakt 1 Ziel pro Buchung |
| Prognose-Integration: aktive Ziele mit Rate → deterministische geplante Ausgaben in `finance_cash_flow_forecast` | Kündigungsservice, Cashback, externe APIs, Cloud |
| UI: Sub-Tab „🎯 Sparziele" (Fortschritt, benötigte Rate, ETA, On/Off-Track, Kandidaten mit Annahme) | Änderungen an Phase-1-Methodik (Schedule-first-Hybrid bleibt unverändert) |
| i18n DE/EN/BG (`finance_ui.goals.*`), Tests, Doku (03 §19, funktionen.md) | Budget-Änderungen (`budgets`-Tabelle bleibt unverändert) |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Frische DB: `goals` + `goal_contributions` + Indexe vorhanden | pytest (tmp-DB, `sqlite_master`) | ✅ 2026-09-15 (TestDoD1_Schema) |
| 2 | Altdatenbank (ohne Ziele-Tabellen): Tabellen beim nächsten `FinanceDB()`-Init automatisch angelegt, keine Datenverluste | pytest (init DB mit altem Zustand, dann re-init) | ✅ 2026-09-15 (test_reinitialized_db_keeps_goal_data) |
| 3 | DAO: Goal create/update/status, Zuordnung/Entzug deterministisch; `UNIQUE(transaction_id)` erzwingt max. 1 Ziel pro Buchung (konstruktiver Konflikt-Test); Cascade-Delete; Fortschritt = Σ Zuordnungen (keine Double-Counting-Pfade) | pytest (DAO-Suite) | ✅ 2026-09-15 (TestDoD3_DaoInvariants; UNIQUE/CASCADE per SQLite-Metadaten geprüft) |
| 4 | Ziel-Tools `finance_list_goals`/`finance_get_goal`/`finance_project_goal` (final statt geplanter `finance_goals_overview`): Fortschritt, benötigte Monatsrate, ETA, On/Off-Track — deterministisch, injizierbare Referenzdaten, keine Float-Arithmetik auf Cents | pytest | ✅ 2026-09-15 (TestDoD4_GoalTools, TestDoD4_Projection; Rate: explizit > geplant > Historie) |
| 5 | `finance_suggest_goal_candidates` (final statt geplanter `finance_sinking_fund_candidates`): stabile wiederkehrende Ausgaben mit Periode > 45 Tage → Kandidat mit empfohlenem Monatsbetrag (`monthly_equivalent`); monatliche Ausgaben NICHT als Kandidaten; `min_occurrences` ≥ 2 (Default 3), `reference_date` optional, kein `window_days` | pytest | ✅ 2026-09-15 (TestDoD5_Candidates) |
| 6 | Prognose-Integration: aktives Ziel mit `monthly_rate_cents` → geplante Ausgabe im `finance_cash_flow_forecast`-Horizont (bis `target_date`/Ziel erfüllt); Transfer-Ausschluss + Multi-Currency bleiben erhalten | pytest (goals-Suite, TestDoD6) | ⚠️ TEILWEISE 2026-09-15: Overlay (`goals_draw`, `net_with_goals`, kumulatives `balance_with_goals`) + Invarianten ✅; **Stopp am Zielbetrag/-termin NICHT implementiert** (03 §19 deklariert dies ausdrücklich) — offenes Kriterium, siehe „Offene Kriterien“ |
| 7 | Registrierung: Schemas + Toolkit-Dispatch (Fail-Fast) + `FINANCE_ANALYTICS` (read) / `FINANCE_WRITE` (write — nie ReAct-Pool). Finale-Vertrag 2026-09-15: `get_tool_schemas()` + `get_available_tool_schemas()`; Chat-Verfügbarkeit über den Schema-Katalog (keine expliziten Routing-Einträge, vgl. Kommentar in `finance/chat.py`) | pytest (TestDoD7_Registration) + Modul-Import | ✅ 2026-09-15 (Schema-Katalog 10/10, `agent_toolkit.py`-Dispatch 10/10, `FINANCE_ALL` vollständig, `finance_tab` read-only) |
| 8 | UI „🎯 Sparziele" rendert (Ziele-Tabelle + Fortschritt, Kandidaten + Annahme, Zuordnungs-Steuerung) | py_compile + Code-Review | ❌ OFFEN 2026-09-15: kein Goal-Code in `finance/tab.py` (Grep: 0 Treffer) |
| 9 | i18n DE/EN/BG vollständig (inkl. `finance_ui.goals.*`), JSON valide | json.load + i18n-Suite | ❌ OFFEN 2026-09-15: kein `goals`-Key unter `finance_ui` in de/en/bg (JSON selbst valide; Keys: import/main/tabs/accounts/analytics/forecast/…) |
| 10 | Finance-Testsuite grün, keine Regressionen | pytest (Projekt-venv `venv_bot_20260802`) | ✅ 2026-09-15 (reproduziert): goals-Suite 43/43; gemeinsamer Lauf 11 Finance-/Profil-Dateien 211/211. Kein Gesamtprojekt-Release-Gate |
| 11 | Doku: `docs/03_FINANCE_MODULE.md` §19, `funktionen.md` (neuer §), `00_CONTEXT_MASTER` (Tool-Zähler + Changelog), Workdoc archiviert | Review | ⚠️ TEILWEISE 2026-09-15: 03 §19 ✅, `funktionen.md` § „Sparziel-Projektion: Reparaturstand 2026-09-15“ ✅; 00_CONTEXT_MASTER-Changelog ❌ (bewusst erst nach Phase-Abschluss), Archivierung ❌ (so lange DoD#6/8/9 offen) |

## Alternativen & Entscheidung (DB-Design, 5 Kategorien, 1–7)

Kategorien: Korrektheit, Robustheit, Wartbarkeit, Performance, Migrationsrisiko (7 = beste).

### Entscheidung 1: Zielmodell (Zuordnung Buchungen↔Ziel)

| # | Option | Korrektheit | Robustheit | Wartbarkeit | Performance | Migrationsrisiko | Entscheidung |
|---|--------|-------------|------------|-------------|-------------|------------------|--------------|
| A | **`goals` + `goal_contributions` mit `UNIQUE(transaction_id)`** — exakt 1 Ziel pro Buchung; Fortschritt = Σ zugeordneter Beträge | 7 (keine Double-Counting-Pfade; Constraint erzwingt Invariante in der DB) | 7 (IntegrityError → strukturierter Tool-Fehler, vgl. `transfer_links`-Pattern) | 7 (2 Tabellen, Pattern aus `budgets`/`transfer_links`) | 7 (Index auf `transaction_id`) | 6 (additive Migration via `CREATE TABLE IF NOT EXISTS`) | **GEWÄHLT** |
| B | M2M `goal_assignments` mit Beträge-Split (eine Buchung auf mehrere Ziele, Summe ≤ Buchungsbetrag) | 4 (Double-Counting nur per App-Logik vermeidbar) | 5 | 4 (Split-Logik in DAO + UI + Tools) | 6 | 5 (Schema später nur mit Daten-Migration änderbar) | Abgelehnt für v1 (Re-Option bei nachweisbarem Bedarf) |
| C | Heuristik-first: keine DB, Ziele = abgeleitete „virtuelle" Recurring-Gruppen pro IBAN | 4 (kein persistenter Fortschritt, kein User-Intent) | 4 | 5 | 6 | 7 | Abgelehnt (Nutzer-Entscheidung 2026-09-12: DB-backed) |

### Entscheidung 2: Herkunftsmodell der Fortschritts-Buchungen

| # | Option | Korrektheit | Robustheit | Wartbarkeit | Performance | Migrationsrisiko | Entscheidung |
|---|--------|-------------|------------|-------------|-------------|------------------|--------------|
| A | **Explizite Zuordnung** (`source='user'`) + **read-only Vorschläge** (`finance_sinking_fund_candidates` deterministisch aus Historie; Annahme = `goal_create` + `assign`, `source='rule'`) | 7 (User-Intent persistiert; keine stille Zurechnung) | 7 (keine Auto-Zuordnung → kein Silent-Fallback-Smell) | 7 | 7 | 6 | **GEWÄHLT** |
| B | Auto-Zuweisung per Gegenparte-/Verwendungs-Regeln (LLM oder Regex) | 5 (Fehlzuordnungs-Risiko bei echten Geldbeträgen) | 4 | 4 | 5 | 4 | Abgelehnt (verstößt gegen „keine silent fallbacks") |

### Entscheidung 3: Prognose-Integration (Synergie mit Phase 1)

| # | Option | Korrektheit | Robustheit | Wartbarkeit | Performance | Migrationsrisiko | Entscheidung |
|---|--------|-------------|------------|-------------|-------------|------------------|--------------|
| A | **Aktive Ziele mit `monthly_rate_cents` als deterministische geplante Ausgaben in `finance_cash_flow_forecast`** (Horizont begrenzt auf `target_date` bzw. Ziel-Erreichen; nur Ziel-Währung = Prognose-Währung, `status='active'`) | 7 (Sinking-Fund-Semantik: geplante Ausgaben sind der Kern; deterministisch) | 6 (neue Input-Größe → Invarianten-Tests nötig) | 6 | 7 | 5 (additive: ohne Ziele = exakt Phase-1-Ergebnis) | **GEWÄHLT** |
| B | Ziele nur als separate Ansicht, Prognose unverändert | 6 (korrekt, aber „Sinking Fund" ohne Prognose-Wirkung unvollständig) | 7 | 7 | 7 | 7 | Fallback, wenn A Regressionen zeigt |

### Entscheidung 4: Zeitsemantik (YNAB/PocketSmith-Konsens)

- `target_cents > 0` (CHECK), `target_date` optional (ISO `YYYY-MM-DD`),
  `monthly_rate_cents` optional (≥ 0, CHECK),
  `status IN ('active','paused','achieved','archived')`.
- **Benötigte Monatsrate** = `(target − Fortschritt) / max(1, volle Monate bis target_date)`
  (nur wenn `target_date` gesetzt, sonst `null`).
- **ETA** = `(target − Fortschritt) / monthly_rate` Monate (nur wenn Rate > 0
  und Fortschritt < Ziel, sonst `null`).
- **On/Off-Track** (nur mit `target_date`): Fortschritt ≥
  `target / volle Monate bis Datum` → `on_track`, sonst `behind`;
  Fortschritt ≥ 100 % → „achieved"-Kandidat (Tool meldet, User bestätigt
  Statuswechsel — keine automatische Statusänderung).


## Verifizierte Fakten

| # | Fakt | Beleg (Datei:Zeile / Symbol) |
|---|------|------------------------------|
| 1 | Neue Tabellen in `_SCHEMA_STATEMENTS` (`CREATE TABLE IF NOT EXISTS`) werden bei jedem `FinanceDB()`-Init auf frischen UND alten DBs angewendet — additive Migration ohne Datenverlust | `finance/db_schema.py` `_SCHEMA_STATEMENTS`, `_init_schema()` |
| 2 | Cents-Integer, ISO-Daten-TEXT, `source`-Feld ('user'/'rule'/'llm'/'auto'), `created_at` DEFAULT — etablierte Konvention | `finance/db_schema.py` (`budgets`, `transfer_links`, `statement_settlements`) |
| 3 | `UNIQUE`-Constraint + IntegrityError → strukturierter Tool-Fehler ist etabliertes Pattern (Konflikt-Test existiert) | `finance/tools.py` `link_transfer` (`error_class: "conflict"`); `tests/test_finance_transfer_link_conflict.py` |
| 4 | DAO-Pattern: Keyword-Args, `ValueError` bei ungültigen Parametern, `with self._lock, self._connect()`, SELECT/UPDATE/INSERT | `finance/db_schema.py` `upsert_budget` (Zeile 2673) |
| 5 | Tool-Pattern: `success/error/error_class`-Envelope, `_to_cents`/`_from_cents`, `_resolve_account_id(iban)` | `finance/tools.py` `set_budget`/`budget_status` |
| 6 | Phase-1-Recurring-Gruppen (`_recurring_groups`) sind die Basis für Sinking-Fund-Kandidaten (Perioden-Erkennung existiert) | `finance/tools.py` `_recurring_groups` (Phase 1, 2026-09-12) |
| 7 | Write-Tools gehören ins `FINANCE_WRITE`-Profil, nie in den ReAct-Pool (Progressive Tool Disclosure) | `agent/tool_profiles.py`, AGENTS.md |
| 8 | Keine bestehenden Goals-/Sinking-Konzepte im Code (keine Namens-/Semantik-Kollision) | Grep `sinking\|sparziel\|goal` über `finance/tools.py`, `finance/tab.py`, `agent/tool_schemas.py` (2026-09-12: keine Treffer) |

## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | 1 Buchung → max. 1 Ziel ist für reale Nutzung ausreichend (geteilte Sparziele sind Seltenheitsfall) | OFFEN | Nutzer-Fall: gemeinsame Ziel-Beiträge mit Split — wenn auftritt: M2M-Option B (Entscheidung 1) nachziehen |
| 2 | Jährl./halb-jährl. Recurring-Gruppen (Periode > 45 Tage) sind ein verlässliches Kandidaten-Signal | VERIFIZIERT 2026-09-15 | Test: einmalige Großeinkäufe mit zufälliger Wiederholung erzeugen KEINEN Kandidaten (Min. 2 Buchungen + Perioden-Stabilität) — abgedeckt durch `TestDoD5_Candidates` (Occurrence-Grenzen, Periode > 45 Tage, Multi-Currency) |
| 3 | Ziele als Prognose-Ausgaben erhöhen die Nützlichkeit ohne Invariante-Verletzung | VERIFIZIERT 2026-09-15 | monarch-core-Suite-Erweiterung: mit + ohne Zielen → Invarianten (CI-Reihenfolge, Transfer-Ausschluss, Multi-Currency) grün — abgedeckt durch `TestDoD6_ForecastIntegration` (default_excludes_goals, include_goals_adjusts_months, paused/achieved_ignored); gemeinsamer Lauf 211/211 |

## Offene Fragen

| # | Frage | Owner | Deadline | Status |
|---|-------|-------|----------|--------|
| 1 | `finance_goal_create` auch aus dem Chat erreichbar (Write-Tool) oder nur UI? | Nutzer | vor Implementierung | GELÖST 2026-09-15 (Finale-Vertrag): Write-Tools NUR via `FINANCE_WRITE_TOOLS`/`FINANCE_ALL` (dedizierte Finance-Pipeline), nie im ReAct-Pool; Finance-Chat-Verfügbarkeit über den Schema-Katalog (implizite Auswahl, vgl. `finance/chat.py`); UI-Aufgabe bleibt DoD#8 |
| 2 | Statuswechsel `achieved`: automatisch bei ≥ 100 % oder nur User-Bestätigung? | Nutzer | vor Implementierung | GELÖST 2026-09-15: nur User-Bestätigung via `finance_set_goal_status` (Write-Tool); `finance_project_goal` meldet Erreichung (`achieved_month`), ohne automatischen Statuswechsel (Tests: `test_set_goal_status`, `test_projection_achieved_and_overdue`) |
| 3 | Max. Anzahl aktiver Ziele pro IBAN (UI-/Performance-Grenze)? | Agent | Implementierung | GELÖST 2026-09-15 (Code-Review): keine harte Grenze in DAO/Overlay (Summation pro Währung); bei UI-Einführung (DoD#8) neu bewerten |


## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmaßnahme | Status |
|---|--------|--------------------|------------|--------------------|--------|
| 1 | Additive Migration bricht auf einer Altdatenbank (Namens-/Constraint-Kollision) | NIEDRIG | HOCH | DoD#2-Test initiiert eine „alte" DB und re-inits; Rollback = Tabellen dropen (keine Daten) | offen |
| 2 | Double-Counting (Buchung zählt in Prognose UND als Ziel-Beitrag) | MITTEL | MITTEL | Invariante-Test: Prognose-Ausgaben = Phase-1-Basis + Ziel-Raten (exakt); Fortschritt = nur `goal_contributions` | offen |
| 3 | Write-Tools im ReAct-Pool landen (Halluzinierte Ziel-Anlage im Chat) | NIEDRIG | HOCH | `FINANCE_WRITE`-Profil-Test (Muster aus `set_budget`); DoD#7 | offen |
| 4 | Kandidaten-Heuristik erzeugt False-Positives (vertrauensschädigend) | MITTEL | NIEDRIG | Read-only, explizit „Vorschlag"; Min. 2 Buchungen + Perioden-Stabilität; DoD#5 | offen |
| 5 | Prognose-Tool wird langsamer (Ziele-Query) | NIEDRIG | NIEDRIG | Index `idx_goals_status`; Ziele-Query < 1 ms bei < 100 Zielen | offen |

## Sicherheits- & PII-Implikationen

| # | Aspekt | Implikation | Gegenmaßnahme |
|---|--------|-------------|---------------|
| 1 | Finance-Daten bleiben lokal (SQLite, `.db_root`) | Keine neuen externen Abhängigkeiten, keine Cloud | Bestehendes `db_path_resolver`-Pattern unverändert |
| 2 | Ziele enthalten ggf. sensible Kontexte (z. B. „Therapie", „Op-Kosten") | `notes`-Feld: PII-Minimierung | PII-Schutz-Modul greift wie bei bestehenden Textfeldern |
| 3 | Write-Tools im Chat | Nutzer kann Ziele anlegen/löschen — nur Bookkeeping, keine Finanztransaktionen | Fail-Fast-Validatoren, `source='user'`-Audit-Spur, `updated_at` |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `finance/db_schema.py` | `goals` + `goal_contributions` in `_SCHEMA_STATEMENTS` + Indexe; Dataclasses `Goal`/`GoalContribution`; DAO: `upsert_goal`, `list_goals`, `goal_progress`, `assign_contribution`, `unassign_contribution`, `set_goal_status`, `delete_goal` | ✅ 2026-09-15 (goals-Suite 43/43; Pylance: 10/10 `upsert_goal`-Aufrufe kompatibel; Kandidaten-Query liegt in `tools.py`) |
| 2 | `tests/test_finance_goals_schema.py` | Neue Suite: DoD#1–7 (Schema, Migration, DAO-Invarianten, Goal-Tools, Kandidaten, Forecast-Overlay, Registrierung) | ✅ 2026-09-15: 43/43 PASS (reproduziert) |
| 3 | `finance/tools.py` | +10 Tools (5 read, 5 write) + Prognose-Overlay in `finance_cash_flow_forecast` | ✅ 2026-09-15 (TestDoD4/5/6, TestDoD7) |
| 4 | `tests/test_finance_goals_schema.py` (TestDoD6) | Ziele-Prognose-Overlay-Tests (DoD#6, Invarianten +/− Ziele) | ✅ 2026-09-15 (monarch-core-Suite selbst unverändert, 211er-Lauf grün) |
| 5 | `agent/tool_schemas.py`, `agent_toolkit.py`, `agent/tool_profiles.py` | Registrierung (DoD#7): Schema-Katalog, Toolkit-Dispatch, `FINANCE_ANALYTICS`/`FINANCE_WRITE`/`FINANCE_ALL` | ✅ 2026-09-15 (TestDoD7_Registration; `finance/chat.py`/Reflector/Grammar für Goals NICHT geändert — Chat-Verfügbarkeit via Schema-Katalog) |
| 6 | `finance/tab.py` | Sub-Tab „🎯 Sparziele" (DoD#8) | ❌ OFFEN — kein Goal-Code vorhanden |
| 7 | `i18n/locales/{de,en,bg}.json` | `finance_ui.goals.*` + Tab-Key (DoD#9) | ❌ OFFEN — kein `goals`-Key vorhanden (JSON valide) |
| 8 | `docs/03_FINANCE_MODULE.md`, `funktionen.md`, `docs/00_CONTEXT_MASTER.md` | §19 / neuer § / Tool-Zähler + Changelog (DoD#11) | ⚠️ TEILWEISE — 03 §19 ✅, funktionen.md ✅; 00_CONTEXT_MASTER-Changelog + Archivierung erst nach Abschluss offener Kriterien |

## Offene Kriterien & nächster Schritt (Stand 2026-09-15)

| # | Offenes Kriterium | Beleg | Nächster Schritt |
|---|-------------------|-------|------------------|
| 1 | **DoD#6:** Forecast-Overlay stoppt NICHT am Zielbetrag/-termin (zieht die volle Monatsrate über den ganzen Horizont) | `finance/tools.py` `cash_flow_forecast`: `goals_draw` = volle `monthly_rate_cents` je Monat; 03 §19 deklariert dies ausdrücklich als nicht implementiert | **Nächster gezielter Schritt:** `goals_draw` je Monat auf den restlichen Bedarf kappen (`target_cents − saved_cents` kumulativ) und ab Erreichungs-/`target_date`-Folgemonat stoppen. **Prüfkriterium:** neuer Test in `TestDoD6_ForecastIntegration`: (a) Ziel 1000 € @ 400 €/Monat → Draws 400, 400, 200, 0, 0 (kumulativ); (b) `target_date` vor Horizonende → ab Folgemonat des Zielmonds kein Draw; (c) Invarianten (CI-Reihenfolge, Transfer-Ausschluss, Multi-Currency) + goals-Suite 43/43 + 211er-Lauf bleiben grün |
| 2 | **DoD#8:** UI-Sub-Tab „🎯 Sparziele" fehlt | Grep `goal` in `finance/tab.py`: 0 Treffer | Scope-Entscheidung des Nutzers, dann Implementierung + Test |
| 3 | **DoD#9:** `finance_ui.goals.*` i18n-Keys fehlen | de/en/bg.json: kein `goals`-Key unter `finance_ui` | folgt auf DoD#8 |
| 4 | **DoD#11:** 00_CONTEXT_MASTER-Changelog-Zeile fehlt; Workdoc nicht archiviert | 00_CONTEXT_MASTER: keine Phase-2-Zeile (Phase-1-Zeile 2026-09-12 vorhanden) | erst nach Abschluss offener Kriterien — bewusste Entscheidung, keine vorzeitige „fertig"-Markierung |
| 5 | Pylance-Typmeldungen (vorhanden, nicht abschließend bereinigt); mypy nicht im venv | — | exakte Meldungen aus VS Code benötigen, dann punktuell beheben |

## Rollback-Strategie

| Schritt | Aktion | Befehl / Referenz |
|---------|--------|-------------------|
| 1 | Code-Rollback (Tools/UI/DAO) | `git -C <PROJEKT_ROOT> checkout <sha> -- finance/ agent/ i18n/` (Autosave-Snapshot, AGENTS.md §Dateiintegrität) |
| 2 | DB-Rollback (falls nötig) | `DROP TABLE IF EXISTS goal_contributions; DROP TABLE IF EXISTS goals;` — rein additive Tabellen, keine Abhängigkeiten → kein Datenverlust |
| 3 | Doku-Rollback | Workdoc + §19-Änderungen revertieren (git) |

## Testergebnisse

| # | Test / Befehl | Ergebnis |
|---|---------------|----------|
| 1 | `pytest tests/test_finance_goals_schema.py -q` (Projekt-venv `venv_bot_20260802`, 2026-09-15) | ✅ 43/43 PASS (reproduziert) |
| 2 | Gemeinsamer Lauf (2026-09-15): goals, monarch_core, analytics_tools, consistency ×3, chat, structured_runtime, tab_regressions, tool_profiles, tool_profile_gating | ✅ 211/211 PASS (70 s, reproduziert) |
| 3 | `python -m py_compile finance/tab.py finance/tools.py finance/db_schema.py agent/tool_schemas.py agent/tool_profiles.py agent_toolkit.py` (2026-09-15) | ✅ OK |

Kein Gesamtprojekt-Release-Gate; keine produktiven DB-Änderungen, keine LLM-/GPU-Läufe.