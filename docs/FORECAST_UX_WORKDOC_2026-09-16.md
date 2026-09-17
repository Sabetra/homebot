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
| 2026-09-16 | AP1 ABGESCHLOSSEN (alle DoD-Punkte ✅). Commit 832c2f4, Push auf origin/main |
| 2026-09-16 | AP2 S1 (Engine): `finance/series_engine.py` implementiert + `tests/test_series_engine.py` 60/60 PASS; 5 Root-Cause-Fixes (Details unten). Commit `44eb8dc` (Pre-Commit-Gate grün: Secret-Gate + Lizenz-Gate + deterministisches Release-Gate/pytest inkl. der 60 neuen Tests) + Push auf `origin/main` |
| 2026-09-17 | AP2 S1 (DAO): `tests/test_finance_series_dao.py` **55/55 PASS** — CRUD/Locking/Idempotenz/Kandidaten/Ausnahmen/Links/Journal+Undo + Engine↔DAO-Integration; DAO-Bug root-cause-fixt (`undo_series_change` Restore: `tuple + list` ⇒ `TypeError`, Fix + Regressions-Test) |

## AP2 — Serien, Erkennungen und Ist-Abgleich (Start 2026-09-17)

### AP2-Auftrag (Prompt §6 AP2, Zeile 163)

* Serienexpansion, Ausnahmen und Geltungsbereiche implementieren
* Erkennungskandidaten mit Quellen; Bestätigen/Ablehnen/Korrigieren; Pause/Ende
* Kalender listet **ALLE** Vorkommen im Fenster (F01)
* Abo-Monatsäquivalent ≠ echte Fälligkeitssumme (F02); saubere Labels
* F01–F04/F08 **am gemeinsamen Modell** beheben (Prompt §5.2 kanonische
  Planvorkommen-Folge), nicht nur Tabellenzahlen
* Ist-Matching: konservative Kandidaten + Nutzerbestätigung; Mehrdeutigkeit
  melden; Reimport darf manuelle Serie/Ausnahme NICHT löschen
* Ausschluss vom statistischen Rest + stabile Quellenbindung testen
* Wiederkehrende Einnahmen (Gehalt) explizit planbar

**Gate (AP2):** bestätigte Quartalsrechnung → richtige Vorkommen/Kosten
(120/Quartal ⇒ 40/Monat, 480/Jahr, nächste Fälligkeit korrekt);
Skip/Verschieben ändert Kalender, Monatsnetto und Saldo exakt einmal;
gleiche Empfänger verschiedener Konten/Währungen bleiben isoliert.

### AP2-Baseline (Code-Fakten, 2026-09-17)

| Fakt | Beleg |
|------|-------|
| `upcoming_bills` projiziert exakt 1 `next_due` je Gruppe; `total_in_window` = nur deren Summe (F01) | `finance/tools.py:788-870` |
| `_next_due_on_or_after` behandelt alles als monatsbasiert; Quarterly/Weekly/Jährlich fachlich falsch (F01) | `finance/tools.py:637-654` |
| `subscription_audit`: `monthly_cost` = Betrag pro Zahlung, `annual_cost = monthly * 12` — Quarterly 120 ⇒ 120/Monat, 1440/Jahr (F02; Soll: 40/Monat, 480/Jahr) | `finance/tools.py:1387-1395` |
| `_recurring_groups` gruppiert NUR `(currency, counterparty)` — kein Konto (F03); `amounts = [abs(...)]` mischt beide Vorzeichen (F04) | `finance/tools.py:2863-2893` |
| `list_analysis_facts` liefert `transaction_id`, aber KEINE `account_id`/`iban` (F03) | `finance/db_schema.py:2287-2351` |
| F08: Forecast fit historische Monatsrate; Serie erscheint zusätzlich zum gefitteten Pendant (Doppelzählung, §5.2) | `finance/tools.py:872+` |
| AP1-Patterns wiederverwendbar: `forecast_plan_items`-Schema (L536-576), DAO (L3379-4000), `PlanRevisionConflict` (L223), i18n `finance_ui.forecast.plan.*` | `finance/db_schema.py` |
| Test-Setup: `FinanceDB(str(tmp_path/…))`, `upsert_bank`/`upsert_account`, `persist_statement_import` | `tests/test_finance_plan_items.py` |

### AP2-Stufenplan (jeweils grün validiert)

| Stufe | Inhalt | Status |
|-------|--------|--------|
| S1 | Gemeinsames Modell: `recurring_series` + `series_exceptions` + `series_candidates` + `series_occurrence_links` + Journal (Schema/DAO), `finance/series_engine.py` (Expansion/Detektion/Matching, rein deterministisch), Tools-API (list/confirm/reject/pause/end/skip/move/amount/calendar/detect) | Engine fertig (60/60 grün); DAO implementiert & getestet (58/58); Tools-API offen |
| S2 | F01/F02-Fix in `upcoming_bills`/`subscription_audit` (additive Keys + korrekte Fenster-/Monatäquivalent-Logik), F08: opt-in `include_series` in `cash_flow_forecast` (Byte-Kompatibilität Default) | offen |
| S3 | UI (Serien-/Kandidaten-Sektion in Forecast-Tab) + i18n `finance_ui.forecast.series.*` DE/EN/BG + AppTest + Vollvalidierung | offen |

### AP2-Entscheidungen (bewusst, dokumentiert)

| # | Entscheidung | Begründung |
|---|-------------|------------|
| S1-1 | Neue Tabelle `recurring_series` (keine Wiederverwendung von `forecast_plan_items`) | Serie ≠ Einmalplanung: Rhythmus/Anker/Ausnahmen/Quellen sind eigene Semantik (§5.1) |
| S1-2 | Ausnahmen pro (series_id, ORIGINAL-Termin, Typ): skip/move/amount; Move ändert Identität nicht | §5.1: „Verschieben ändert nicht die Identität; erneute Expansion erzeugt weder Dublette noch Verlust der Ausnahme" |
| S1-3 | Kandidaten-Fingerprint aus fachlichem Scope (iban, currency, direction, counterparty, cadence, anchor) — kein Zeilenindex/Anzeigetext | §5.1: „Fingerprint aus fachlichem Scope und Quellen" |
| S1-4 | 2+ Beobachtungen ⇒ Kandidat (Vorschlag), NIE automatische Bestätigung | §5.4: „Zwei Buchungen erzeugen einen prüfbaren Vorschlag, keinen sicheren Vertrag" |
| S1-5 | Ist-Matching 1:1 (UNIQUE beide Seiten); Mehrdeutige/gesplittete Treffer nur melden, nie verknüpfen | §5.1: „vorerst nur 1:1 Abgleich" |
| S1-6 | `list_analysis_facts` um `account_id`/`iban` ERWEITERN (additive Keys, bestehende Keys unverändert) | F03-Fix am Modell, nicht an der Oberfläche |

### S1-Fortschritt: series_engine (Fix-Session, 2026-09-16)

**Status:** `finance/series_engine.py` (659 Zeilen, rein deterministisch, keine
DB-Abhängigkeit) implementiert und grün validiert:
`tests/test_series_engine.py` **60/60 PASS** (0.3 s), `py_compile` OK.
Die 5 Test-Fehlmuster des vorherigen Arbeitsstands sind beseitigt (Details unten).

**Public API (Stage 1):**

| API | Zweck |
|-----|-------|
| `clamp_day(year, month, anchor_day)` | Anker-Tag → Kalendermonat; Anker 29..31 ⇒ Monatsende (2026-06-29 ⇒ 2026-06-30, Anker 31 im Februar ⇒ 28/29) |
| `SeriesSpec` (frozen) | validierte Serien-Definition: cadence, period_n, anchor, effective_from/to, amount_cents > 0 (Vorzeichen aus direction) |
| `SeriesException` (frozen) | skip / move / amount pro (series_id, ORIGINAL-Termin); Move ersetzt nur das Datum (Identität bleibt) |
| `expand_series(spec, window, exceptions)` | deterministische Expansion; sortiert, dedupliziert, Cap `_MAX_OCCURRENCES` (2000) |
| `next_occurrences(spec, count, on_or_after)` | nächste N Vorkommen (Suchfenster 3 Jahre; respektiert effective_to + Ausnahmen) |
| `candidate_fingerprint(...)` | stabiler 8-Dimensionen-Scope: iban, currency, direction, counterparty, cadence, period_n, anchor_day, anchor_date — normalisiert, kein Anzeigetext |
| `estimate_cadence(dates)` | konservativer Schätzer (Wochen-Multiplen mit k=1-Mehrheit, Kalendermonate mit Lücken-Toleranz, yearly); inkonsistent ⇒ None |
| `detect_candidates(facts)` | Gruppierung nach (iban, currency, direction, counterparty); 2+ Belege ⇒ prüfbarer Kandidat (status 'pending', NIE auto-confirmed); Betrag = aktuellste Beobachtung (F02) |
| `match_occurrence` / `match_occurrences` | konservatives 1:1; Standard-Fenster ±1 Tag; Mehrdeutigkeit bleibt ungematcht (nur melden, nie raten) |

**Fixes dieser Session (5 Root-Causes, jeweils mit Test abgedeckt):**

| # | Alt-Verhalten (Root Cause) | Neu-Verhalten |
|---|---------------------------|---------------|
| 1 | `clamp_day` clampete nur überlappende Tage (min-Anker): Anker 29 blieb im Juni 29 | Anker ≥ 29 ⇒ Monatsende (Prompt-Beispiel: 2026-06-29 ⇒ 2026-06-30) |
| 2 | `estimate_cadence` (Wochen): Einzel-Rundung `round(mean/7)` + ±2-Tage-Toleranz ⇒ Misch-Multiplen (z. B. 7 + 14 Tage) fielen durch, 14-Tage-Rhythmus konnte als Monat durchgehen | exakte 7-Tage-Multiplen mit k=1-Mehrheit (14-Tage-Rhythmus ⇒ `n_weeks 2`); Monats-Branch nur noch bei stabiler Tag-Komponente (±3 Tage) |
| 3 | `estimate_cadence` (Monate): Anker = `max(Tage)`, keine Lücken-Toleranz ⇒ unregelmäßige Beobachtungen lieferten falschen Rhythmus oder keinen | Anker = häufigster Tag (Majority); komplette Kalendermonate mit ganzzahliger Lücken-Toleranz (Lücken = ganze Perioden) |
| 4 | `detect_candidates`: Scope-Normierung (IBAN/Counterparty) driftete gegenüber dem Fingerprint (F03) | eine Normierung für Gruppierung, Kandidaten-Felder und Fingerprint (`_norm_scope` + `candidate_fingerprint` auf denselben Werten) |
| 5 | `match_occurrences`: Standard-Fenster ±7 Tage zu großzügig für 1:1 (T8) | Standard-Fenster ±1 Tag; pro Beleg bleibt `tolerance_cents` explizit setzbar |

**Arbeitsbaum-Status (wichtig für nächste Session):**
`finance/db_schema.py` enthält +1505 Zeilen Serien-DAO aus vorangegangener Arbeit
(Tabellen `recurring_series`, `recurring_series_candidates`, `series_exceptions`,
`series_occurrence_links`, `series_journal`; Upsert/Journal/Undo-Logik) —
**seit 2026-09-17 getestet**: `tests/test_finance_series_dao.py` **55/55 PASS**
(CRUD + Fail-Fast-Validierung, Optimistic Locking, `client_token`-Idempotenz,
Kandidaten-Lifecycle, Ausnahmen skip/move/amount, Occurrence-Links 1:1,
Journal/Undo inkl. Wiederherstellung gelöschter Serien, Engine↔DAO-Integration:
`detect_candidates → save → confirm → series_to_spec → expand_series`).
Dabei gefunden & root-cause-fixt: `undo_series_change` (Restore-Pfad gelöschte
Serie) machte `tuple + list` ⇒ `TypeError`; Fix `list(_SERIES_RESTORE_FIELDS)`,
Regressions-Test `test_undo_restores_deleted_series`.
Zusätzlich gelandet (2026-09-17, jeweils mit Tests): `series_exceptions.note`
(Freitext-Begründung, T11; Migration `_migrate_series_exception_note` für
bestehende DBs), `series_candidates.evidence_json` NOT-NULL (leer → `""`),
`update_candidate_evidence` korrekt auf Spalte `evidence_json`,
`confirm_candidate` liest `cand.evidence_json`, `link_occurrence` aktualisiert
`updated_at`, einheitlicher Mapper `_series_link_from_row`; DAO-Suite damit
55/55 → 58/58 (note-Roundtrip/-Normalisierung/Journal-Payload).
Design-Fakten (bewusst, in Tests dokumentiert): Exception-Upsert ist lenient
(`skip` darf neue Felder tragen — die Engine ignoriert sie), Serien-Default-
Währung = `DEFAULT_CURRENCY` (CHF), NICHT die Kontowährung.
Nächste Session: **S1-Finale = Tools-API** (list series, detect candidates,
confirm/reject, pause/end, skip, move) → S2 (F01/F02/F08).