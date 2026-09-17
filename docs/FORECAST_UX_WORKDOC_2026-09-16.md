<!-- last-verified: 2026-09-16 -->
# WORKDOC — Forecast UX (Plan-Items), 2026-09-16

Aufgabe: `docs_archive/FORECAST_UX_QWEN_IMPLEMENTATION_PROMPT_2026-09-16.md`
Status: AP1 (UI + Core) ABGESCHLOSSEN (2026-09-16, alle DoD-Punkte ✅, 222/222 Tests grün). AP2 (Detektion) offen — bewusst NICHT in diesem Schritt.

---

## AP0 — Baseline & Funde (verbindlich, vor Code)

### 1. Bestehende Prognose-Architektur (Code = Source of Truth)

| Baustein | Ort | Verhalten |
|----------|-----|-----------|
| `upcoming_bills` | `finance/tools.py` | deterministischer Fälligkeits-Kalender |
| `cash_flow_forecast` | `finance/tools.py` (~860–1065) | Schedule-first-Hybrid: recurring (konstante Paare) + variable (Bootstrap, fester Seed) + saisonale Anker; pro Währung |
| Goals-Overlay | `finance/tools.py` (`include_goals`, Default **False**) | Pattern: opt-in, pro Monat `goals_draw`/`net_with_goals`/`balance_with_goals`/`balance_low_with_goals`/`balance_high_with_goals` + top-level `goals`-Objekt |
| `project_goal` | `finance/tools.py` | 12-Monats-Simulation je Sparziel (separat von der Cashflow-Prognose) |
| DB | `finance/db_schema.py` | `goals` + `goal_contributions` (Phase 2, 2026-09-12); Cents-INTEGER, IBAN als SSOT |

### 2. Byte-Kompatibilitäts-Anforderung (Prompt-Gate 2)

Default-Pfad `include_goals=False` + `include_manual_plan=False` muss **exakt** die
bestehende Payload liefern (keine neuen Keys). Neue Keys erscheinen NUR bei
opt-in-Parametern — gleiches Muster wie `goals_draw` heute.

### 3. Externe Änderungen 2026-09-16 in `finance/tools.py`

`git diff` zeigt 179 Insertionen / 62 Deletionen in `finance/tools.py`
(+122/−12 in `finance/db_schema.py`) — diese externen Änderungen müssen
erhalten bleiben: nur **gezielte, additive** Edit-Blöcke einfügen, keine
Datei-Neuausgaben. Vor jedem Edit: exakter Altstand zitieren.

### 4. Invarianten (Prompt-Gate 1 & 4)

* Bank-/Transaktions-Tabellen bleiben unverändert: Plan-Items sind eine
  **eigene Tabelle** (`forecast_plan_items`) — keine Buchungen, kein
  Transaktions-Doppelbeleg.
* `goal_contributions` bleibt der Fortschritt der Sparziele; Plan-Items
  sind **nicht** `goal_contributions` (getrennte Semantik: Plan-Item =
  einmalige Prognoseannahme; Contribution = zugeordnete Buchung).
* Kein Future-Leak: Plan-Items mit `due_date` vor dem Referenzdatum
  fließen nicht in die Prognose; `due_date == Referenz` gilt als
  Restmonat (heute).

### 5. Test-Konventionen

`tests/test_finance_monarch_core.py`: `finance.embeddings` wird per
monkeypatch gestubt, `FinanceDB(str(tmp_path / "x.db"))`,
`FinanceTools(db=...)`, `persist_statement_import` für Seed-Daten.
Neue Suite: `tests/test_finance_plan_items.py` (gleiche Konventionen).

### 6. UI-Zugangspunkte

* Prognose-Tab: `finance/tab.py` (`render`), Prognose-Rendern ab ~Z. 1100;
  Chart-Start-Label = Referenzdatum (Gate: "Startsaldo unter dem ersten
  Prognosemonat" → Fix: Label = Monatsanfang).
* Strings: `i18n/locales/{de,en,bg}.json` unter `finance_ui.forecast.*`
  (DE/EN/BG alle drei pflegen, `i18n/i18n_manager.py`).
* Tool-Dispatch: `AgentToolkit` delegiert `FinanceTools`-Methoden;
  Schemas in `agent/tool_schemas.py` (AP1: UI-first, additive Schemas
  ohne bestehende zu ändern).

---

## AP1 — Design (UI + Core)

### Datenmodell (neue Tabellen, additiv)

```
forecast_plan_items
  id, iban (SSOT), currency, kind ('income'|'expense'),
  amount_cents (>0), category_id (FK categories, SET NULL),
  title, due_date (ISO, NOT NULL), source_type ('manual'|'detected'),
  status ('active'|'paused'|'done'|'rejected'),
  revision (optimistic lock, Start 1), client_token (UNIQUE, Idempotenz),
  notes, created_at, updated_at

forecast_plan_journal   -- Undo via Gegenrevision; ueberlebt Item-Loeschung
  id, plan_item_id (KEIN FK, bewusst), action
  ('created'|'updated'|'deleted'|'restored'),
  before_json, after_json, revision, created_at
```

* Vokabular als Single Source of Truth in `finance/models.py`
  (`VALID_PLAN_ITEM_KINDS`, `VALID_PLAN_ITEM_STATUSES`,
  `VALID_PLAN_ITEM_SOURCES`, `VALID_PLAN_ACTIONS`), `db_schema.py`
  importiert (Muster: `VALID_GOAL_STATUSES`).
* Optimistic Concurrency: Updates verlangen `expected_revision`;
  Konflikt → `PlanRevisionConflict` (keine stille Ueberschreibung).
* Undo = Gegenrevision: `created`→Delete, `updated`/`restored`→
  before-State, `deleted`→Reinsert (gleiche ID).
* `client_token` (UI-Generated) macht Create idempotent (Doppel-Click).
* **Keine Migration noetig**: Freshe-DB-Statements in
  `_SCHEMA_STATEMENTS`; `CREATE ... IF NOT EXISTS` ist fuer
  Altdatenbanken automatisch additiv (analog `transaction_search_docs`).

### API (FinanceTools, alle `{"success": bool, ...}`)

| Methode | Zweck |
|---------|-------|
| `preview_plan_item` | reiner Vorschau-Lauf (keine Schreib-Operation), Draft + bestehende Items, Baseline aus Historie ODER plan-only (0-Baseline; ohne Startsaldo kein Fake-Guthaben) |
| `create_plan_item` | validiert (IBAN bekannt, Betrag > 0, ISO-Datum, Art) + Idempotenz via client_token |
| `get_plan_item` / `list_plan_items` | Lesezugriffe (Filter: iban, status, kind, Zeitraum) |
| `update_plan_item` | `expected_revision` Pflicht; Felder: iban, currency, kind, amount, due_date, title, notes, status |
| `set_plan_item_status` | `expected_revision` Pflicht (active/paused/done/rejected) |
| `delete_plan_item` | Journal `deleted`, Item weg, undo-faehig |
| `undo_plan_change` | letzte Journal-Aktion rueckgaengig (Gegenrevision) |

### Prognose-Erweiterung (`cash_flow_forecast`, additiv)

* `include_manual_plan` (Default **False**): aktive Plan-Items (Quelle
  `manual`, spaeter `detected`) mit `due_date` ab Referenzdatum werden
  pro Waehrung/Monat aggregiert (Einnahme +, Ausgabe −).
  - `rest_month`-Objekt: `period_start=Referenz`, `period_end=Monatsende`,
    `days_remaining`, `manual_plan`, `net_with_plan`, ggf.
    `balance_with_plan` (Restmonat mit Referenzdatum).
  - pro Prognose-Monat: `manual_plan`, `net_with_plan`,
    `balance_with_plan`, `balance_low_with_plan`,
    `balance_high_with_plan` (Nur wenn Baseline-Balance vorhanden —
    Kettenrichtigkeit wie Goals-Overlay).
  - top-level `plan`-Overlay: `count`, `items`, `beyond_horizon`.
* `manual_start_balance` (optional): datierte, sichtbare Prognose-Annahme;
  ersetzt den transaktionsbasierten Startsaldo, Kennzeichnung
  `balance.start_balance_source: "manual"`. Ohne Historie + ohne
  Startsaldo: Cashflow-only (kein Fake-Guthaben), `history_months: 0`.
* Determinismus: keine neuen Zufallsquellen; Seed bleibt der bestehende;
  gleiche Eingabe → gleiche Ausgabe; Aggregation sortiert nach
  `(iban, due_date, id)`.

### UI (Streamlit, `finance/tab.py`)

* Prognose-Tab: Abschnitt "Geplante Ein-/Auszahlungen" —
  Erstellen (Art, Betrag, Datum, Titel, Notiz) mit **Vorschau/Speichern/
  Verwerfen**; Liste bestehender Items mit Status, Revision, Aktionen
  (Status, Bearbeiten, Löschen mit Bestätigung, **Rückgängig**).
* Chart: Start-Label = Monatsanfang (Fix), Restmonat als eigener Balken
  neben dem Startsaldo, Spalten "mit Plan" bei aktiven Items.
* i18n: neue Keys unter `finance_ui.forecast.plan.*` (de/en/bg),
  bestehende Keys unverändert.

### DoD — Status 2026-09-16 (alle 6 Punkte ✅)

1. ✅ Plan-Items koennen in der UI erstellt, vorgeprueft, bearbeitet,
   statusgewechselt, geloescht und **rueckgaengig** gemacht werden.
   (`finance/tab.py` §Plan: Form + Vorschau + Speichern/Verwerfen;
   Expander-Liste mit Status-, Loesch-, Undo-Buttons)
2. ✅ Prognose enthaelt `rest_month` + `manual_plan`/`net_with_plan`/
   `balance_with_plan` (opt-in), ohne Plan-Items byte-identisch wie
   vor diesem Schritt. (`test_defaults_stay_byte_compatible` PASS)
3. ✅ Deterministische Wiederholung (gleiche Eingabe → gleiche Ausgabe),
   kein Future-Leak, Bank-/Transaktions-Tabellen bleiben unveraendert
   (`test_deterministic`, `test_items_before_reference_ignored`,
   `test_bank_data_untouched`, `test_forecast_call_is_read_only` PASS)
4. ✅ Neue Regressionen fuer: Erstellen, Preview, Restmonat, Update,
   Status, Loeschen, Undo, Idempotenz, Revision-Konflikt,
   Multi-Currency, Prognose-Mix (Plan + Goals gleichzeitig).
   (`tests/test_finance_plan_items.py`, 42/42 PASS)
5. ✅ DE/EN/BG-Strings voellig, keine toten Keys. (37× `plan.*`-Keys in
   de/en/bg; Parität + `test_i18n_consistency.py` PASS)
6. ✅ `run_pytest_venv.ps1 tests/test_finance_plan_items.py -v` gruen,
   `tests/test_finance_monarch_core.py` +
   `tests/test_finance_tab_regressions.py` gruen, `py_compile` auf
   allen geaenderten Dateien gruen. (Kombi-Lauf: 222/222 PASS)

## Entscheidungen / Annahmen (bewusst, dokumentiert)

| # | Entscheidung | Begruendung |
|---|-------------|-------------|
| D1 | `source_type` enthaelt `detected` von Anfang an; AP2 fuellt es | Schema-Stabilitaet; Prompt verlangt Vorschlags-Pfad ab AP2, aber gleiche Tabelle |
| D2 | `manual_start_balance` wird auch mit `include_manual_plan=False` akzeptiert (reine Balance-Annahme) | Startsaldo ist eine Prognose-Annahme unabhaengig vom Plan |
| D3 | `rest_month` erscheint immer, wenn `include_manual_plan=True` (auch mit 0.0) | stabiles Payload-Schema fuer UI/Tests |
| D4 | Items mit `due_date` vor Referenz werden in der Prognose ignoriert, aber gelistet | kein Future-Leak, kein Past-Leak |
| D5 | `undo_plan_change` greift auf die **letzte** Journal-Aktion pro Item | einfache, deterministische Semantik |
| D6 | Plan-Items haben KEIN `goal_id`-FK | getrennte Semantik (Gate: keine goal_contributions-Mixing) |
| D7 | `client_token` UNIQUE: gleicher Token + andere Payload → vorhandenes Item wird zurueckgeliefert (keine implizite Update) | Idempotenz ohne Ueberraschungen |

## Risiken

* `cash_flow_forecast` ist ein Hot-Spot (extern 2026-09-16 geaendert) →
  nur additive Bloecke, Altstand exakt zitieren, danach Monarch-Core
  Regressionen laufen lassen.
* Chart-Fix in `tab.py` betrifft nur Labels; Render-Pfad nicht beruehren.
* i18n: drei Locale-Dateien synchron halten (Trenner-Regex
  `i18n/i18n_manager.py` beachten — keine neuen Sonderzeichen einbauen).

## Log

| Zeit | Schritt |
|------|---------|
| 2026-09-16 | AP0-Baseline fertig (dieses Dokument) |
| 2026-09-16 | AP1: models.py-Vokabular, db_schema.py (Tabellen/DAO), tools.py (API + Prognose), Tests, UI, i18n |
| 2026-09-16 | Verifikation: 42/42 plan_items, 88/88 monarch_core+tab_regressions, 92/92 erweiterte Suiten, Kombi-Lauf 222/222 PASS; py_compile OK; i18n-Parität 37×3 Locales; check_licenses --strict OK |
| 2026-09-16 | Fix: `finance/tab.py` importierte nicht existierende `RevisionConflictError` (Import + except) → `PlanRevisionConflict` (SSoT `db_schema.py:223`); vorher ImportError bei Collection von `test_finance_tab_regressions.py`, danach grün |
| 2026-09-16 | AP1 ABGESCHLOSSEN (alle DoD-Punkte ✅). Nächste Etappe: AP2 (Detektion) — bewusst nicht in diesem Schritt |