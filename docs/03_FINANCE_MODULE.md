<!-- last-verified: 2026-09-15 -->
# 03 - Finance Module Documentation

> **Stand:** 2026-07-27 | **Code- und Gemma4-Canary-verifiziert**
> **Runtime:** lokal, SQLite, Pydantic v2, Gemma4 12B via llama.cpp
> **Verifikation:** 243 Gesamttests; strikter Finance-Canary (Planner 4/4, Reflector und Full Chat)

---

## 1. Finance Module Architecture

### 1.1 High-Level Flow
```
Natural-Language Query
    |
    v
[Query Planner] --> typisierter Finance-Toolplan mit kanonischen Argumentvertraegen
    |
    v
[Grammar Compiler] --> Pydantic-v2-Schema zu BNF fuer constrained decoding
    |
    v
[Finance Tool] --> deterministische SQLite-/Analyse-Ausfuehrung
    |
    v
[Query Reflector] --> Abschluss oder typisierte Fortsetzung
    |
    v
[Toolfreie Endsynthese] --> Streamlit UI
```

### 1.2 Component Overview

| Component | File | Purpose | Status |
|-----------|------|---------|--------|
| Query Planner | `finance/query_planner.py` | NL zu typisiertem Finance-Toolplan | Verifiziert |
| Grammar Compiler | `finance/grammar_compiler.py` | Pydantic-v2-Schema zu BNF | Verifiziert |
| Query Reflector | `finance/query_reflector.py` | Ergebnisbewertung und Fortsetzungsentscheidung | Verifiziert |
| Tools | `finance/tools.py` | SQLite-Abfragen und deterministische Analysen | 37 exponierte Tools implementiert |
| Tab UI | `finance/tab.py` | Streamlit-Dashboard und Finance-Chat | Verifiziert |
| Chat | `finance/chat.py` | Lokale Finance-Toolschleife und Endsynthese | Python-Executor produktiv gesperrt |
| Extractor | `finance/extractor.py` | PDF zu Transaktionen | Aktiv |
| DB Schema | `finance/db_schema.py` | SQLite-Persistenz und Analyse-Facts | Aktiv |

---

## 2. Code-Qualitäts-Review (2026-07-26)

### 2.1 Kritische Fehler behoben in `finance/db_schema.py`:

| # | Fehler | Severity | Root Cause | Fix |
|---|--------|----------|------------|-----|
| 1 | `_from_cents(0)` returned `None` statt `"0.0"` | KRITISCH | Float(0) ist falschy in Python | Fallback `or "0.0"` |
| 2 | `_to_cents("0.0")` returned `None` statt `0` | KRITISCH | int(round(0.0 * 100)) = 0, falschy | Fallback `or 0` |
| 3 | `_hash_file` auf Text-Modus (Unicode-Corruption) | KRITISCH | `open(path, 'r')` statt `'rb'` | Binary mode |
| 4 | `list_uncategorized` COALESCE Null-Coercion | KRITISCH | `COALESCE(MAX(...), '')` verwandelt None in Empty-String | `CASE WHEN COUNT > 0 THEN MAX ELSE NULL END` |
| 5 | `list_counterparties` COALESCE Null-Coercion | KRITISCH | Gleiches Pattern wie #4 | Gleiches Fix |

**Impact:** Diese 5 Fixes verhindern:
- NullReferenceExceptions bei Null-Beträgen (zero-amount transactions)
- PDF-Hash-Kollisionen durch Unicode-Corruption
- Falsche Daten in UI (Empty-String statt None bei optionalen Feldern)

### 2.2 Zusaetzliche Fixes vom 2026-07-27

- Geldbetraege werden mit `Decimal(str(amount))` und `ROUND_HALF_UP` in Cents konvertiert.
- Budget-Istwerte werden als positive, kategoriegerechte Ausgaben dargestellt.
- Planner und Reflector leiten keine nicht unterstuetzten `grammar_constraint`-Keywords an den LLM-Client weiter.
- Planner-Prompts erhalten kompakte Argumentvertraege direkt aus den kanonischen OpenAI-Toolschemas.
- Fallback-Nutzung und Fehlerursache sind ueber `used_fallback` und `last_error` diagnostizierbar.

---

## 3. Query Planner (`finance/query_planner.py`)

### 3.1 Purpose
Uebersetzt Finance-Fragen in einen validierten `FinanceQueryPlan` fuer genau ein exponiertes Finance-Tool.

### 3.2 Key Features
- Semantic intent recognition
- Toolwahl aus dem aktiven Finance-Schemakatalog
- Argumentextraktion anhand kanonischer Parametervertraege
- Referenzdatumsbasierte Monatsauflösung
- deterministischer, sichtbarer Finance-Tool-Fallback

### 3.3 Code-Qualität
✅ Sauber. Kein Deadcode, keine Workarounds. Gutes Separation-of-Concerns.

---

## 4. Grammar Compiler (`finance/grammar_compiler.py`)

### 4.1 Purpose
Kompiliert Pydantic-v2-Ausgabemodelle in BNF-Grammatiken fuer die llama.cpp-Runtime.

### 4.2 Key Features
- GBNF grammar generation for Pydantic schemas
- Laufzeitvalidierung bleibt im `LLMStructuredWrapper`

### 4.3 Code-Qualität
✅ Sauber. 725 Zeilen, gut strukturiert. Single source of truth für Grammar-Constraints.

---

## 5. Query Reflector (`finance/query_reflector.py`)

### 5.1 Purpose
Implements a self-correction loop that validates query results and iteratively improves incorrect queries.

### 5.2 Key Features
- Result plausibility checking
- Error pattern recognition
- Iterative query refinement
- Confidence scoring

### 5.3 Diagnostik
Der Reflector meldet strukturierte Fallback-Nutzung explizit. Ein stiller Wechsel auf die Fallback-Entscheidung ist damit im Canary und in Tests erkennbar.

---

## 6. Extractor (`finance/extractor.py`)

### 6.1 Purpose
PDF-Kontoauszug-Extraktor mit Docling + LLMStructuredWrapper Pipeline.

### 6.2 Architecture
1. PDF → Docling (Markdown + Tabellen + KG-optimierte Chunks)
2. SHA-256 Hash für Idempotenz
3. Zwei-Phasen-Extraktion:
   - Header-Pass: StatementHeader aus Kopf- und End-Window
   - Transaktions-Pass: Chunk-basierte Extraktion mit token-bewussem Chunking
4. Deduplizierung, chronologische Sortierung
5. FinanceDB.upsert_* Persistenz

### 6.3 Token-Budget-Management
- Adaptive Chunk-Größe basierend auf n_ctx
- `_TX_CHUNK_CHARS = 28_000` mit 800-Char Overlap
- Safety-Margin: 800 Tokens
- Chars-per-Token: 3.8 (deutsches Markdown)

### 6.4 Code-Qualität
✅ Exzellent. 1545 Zeilen, sehr gute Dokumentation, strukturelle statt heuristische Fehlervermeidung.

---

## 7. Categorizer (`finance/categorizer.py`)

### 7.1 Purpose
LLM-gestützte Batch-Kategorisierung unkategorisierter Buchungen.

### 7.2 Workflow
1. `suggest(...)`: Holt unkategorisierte Transaktionen, baut Prompt mit existierenden Kategorien, ruft LLM mit GBNF-erzwungenem JSON auf
2. `apply(...)`: Schreibt Vorschläge in DB, erstellt optional `counterparty_rules` für zukünftige automatische Kategorisierung

### 7.3 Code-Qualität
✅ Sauber. Adaptive Batch-Größe, GBNF-erzwungener Output, kein Keyword/Regex-Fallback.

---

## 8. Cache (`finance/cache.py`)

### 8.1 Purpose
LRU-Cache mit TTL und Warmup-Funktionalität für Query-Results.

### 8.2 Code-Qualität
✅ Sauber. Deterministisches Eviction, Thread-safe.

---

## 9. Tools (`finance/tools.py`)

### 9.1 Purpose
Ausfuehrung von lesenden SQLite-Abfragen und deterministischen Finance-Analysen.

### 9.2 Analyse-Tools
- Kategorie- und Gegenparteikosten
- Fixkosten/variable Kosten und wiederkehrende Ausgaben
- Rolling-Mean-Prognose und populationsbasierte Z-Score-Anomalien
- Budget-vs-Ist, Sparpotenzial und Trendbrucherkennung
- Alle Ergebnisse bleiben waehrungsgetrennt; Transfers sind standardmaessig ausgeschlossen.

---

## 10. Chat (`finance/chat.py`)

### 10.1 Purpose
Finance-Chat-Engine mit Planner, Finance-only Toolausfuehrung, Reflector und toolfreier Endsynthese.

### 10.2 Sicherheits- und Abschlussvertrag
- Produktiv sind ausschliesslich `finance_*`-Tools erlaubt; `code_executor` ist deaktiviert.
- Erfolgreiche direkte Aggregationen werden sofort synthetisiert und oeffnen keine redundante Toolrunde.
- Toolfehler und abgelehnte Tools bleiben im Trace sichtbar.

---

## 11. Models (`finance/models.py`)

### 11.1 Purpose
Pydantic v2 Schemata für LLM-strukturierte Extraktion.

### 11.2 Key Features
- IBAN/BIC Validierung (ISO 13616)
- Datumsnormalisierung (7 Formate)
- Account-Type Vocabulary (checking, credit_card, savings, cash, investment, other)
- Transaction-Nature Vocabulary

### 11.3 Code-Qualität
✅ Sauber. Pydantic v2 konform, keine v1-API.

---

## 12. Token Budget (`finance/token_budget.py`)

### 12.1 Purpose
Single source of truth für n_ctx Resolution in Finance-Modulen.

### 12.2 Resolution Order
1. `llm_client.get_max_context_tokens()`
2. `llm_client._cached_n_ctx`
3. Default: 16384 Tokens

### 12.3 Code-Qualität
✅ Sauber. 40 Zeilen, fokussiert.

---

## 13. Database Schema (`finance/db_schema.py`)

### 13.1 Purpose
SQLite-basierte FinanceDB mit FinanceDB Klasse.

### 13.2 Key Tables
- Banks, Accounts, Statements
- Transactions, Categories
- Counterparty Rules, Reconciliations

### 13.3 Code-Qualität
Null-Handling, Cent-Rundung, Budget-Istvorzeichen und UTC-Zeitstempel sind regressionsgetestet.

---

## 14. Runtime-Vertraege

- Das Finance-Modul verwendet den lokal geladenen Modellclient; Cloud-LLM-Aufrufe sind nicht Teil des Produktivpfads.
- `APP_LOCAL_ONLY=1` wird von den Canary- und Release-Gate-Runnern erzwungen.
- Unterstuetzte Konfiguration wird aus den tatsaechlichen Codepfaden gelesen; nicht implementierte Finance-Environment-Schalter sind kein Vertrag.

---

## 15. Test-Status

Verifiziert am 2026-07-27:

- `python scripts/run_release_quality_gate.py --mode deterministic`: 243 Tests und striktes Profile-Fixture bestanden.
- `python scripts/run_finance_canary.py --model-id gemma-4-12b-it --strict`: Planner 4/4, Reflector und Full Chat bestanden.
- Finance-Fokussuite fuer Analytics, Chat, Planner-Runtime und Tab-Regressions: 16 Tests bestanden.
- Canary-Berichte werden unter `monitoring/finance/`, aggregierte Release-Berichte unter `monitoring/release_quality/` geschrieben.

---

## 16. Cleanup (2026-07-26)

- 8 Backup-Files entfernt: `*.backup`, `*.bak_*`
- Workdoc gelöscht nach Abschluss

---

## 17. Consistency, Prior-Balance, Repair & Settlement (2026-09-09)

### 17.1 Prior-Balance-Lookup (`finance/db_schema.py`)

`FinanceDB.get_prior_closing_balance(account_id, before_period, exclude_statement_id=None)`:

- NUR desselben Kontos; strikte Regel ``period_end < before_period`` (keine Gleichtags- oder ueberlappende Perioden).
- Nur Statements mit **nicht-leerem Endsaldo** kommen in Frage.
- Deterministisch: hoechste ``period_end``, bei Gleichstand hoeheres ``id``.
- ``exclude_statement_id`` schliesst das Statement vom Suchraum aus (Selbstreferenz-Guard im Repair-Pfad).
- Kein Treffer / keine Periode -> ``None`` (niemals ein erfundener Wert).

Konservativer Vertrag: ``None`` -> die Engine markiert ``prior_balance_link`` als ``skipped``
-> ``passed_with_warnings`` + ``needs_review = True``. Fehlender Vorzeitraum bedeutet
review-pflichtig, **kein** impliziter Pass.

### 17.2 Konsistenz-Engine (`finance/consistency.py`)

- `evaluate_statement_consistency(...)`: deterministische, Cents-genaue Pruefungen:
  `credits_total`, `debits_total`, `balance_chain` (Anfangssaldo + Guthaben - Belastungen = Endsaldo),
  `prior_balance_link` (Anfangssaldo == Vorzeitraum-Endsaldo).
- `evaluate_extracted_statement(...)`: Duck-Typing-Adapter fuer `ExtractedStatement` / `StatementHeader` oder Dict.
- `ConsistencyReport`: `status` (passed / passed_with_warnings / failed),
  `needs_review = (status != passed)`, `errors` (fehlgeschlagene Pruefungen),
  `warnings` (uebersprungene Pruefungen), `to_dict()` fuer UI/DB-Storage.

### 17.3 Import vs. Repair

| Pfad | Verhalten |
|------|-----------|
| Import (`persist_statement_import`) | Statement + Buchungen persistieren, Konsistenz pruefen; ohne Vorstatement konservativ review-pflichtig. |
| Repair (`FinanceExtractor.repair_statement_header`) | NUR Header (Bank/Konto/Periode/Salden) per Docling neu extrahieren, Buchungen bleiben unangetastet. Danach: `get_prior_closing_balance(..., exclude_statement_id=stmt)` + `evaluate_extracted_statement` + `update_statement_consistency`. |

Review-Semantik:

- **Klaert**: alle Pruefungen passed (inkl. gueltigem Prior-Link) -> `needs_review = False`, `consistency_errors` wird geleert.
- **Bewahrt**: irgendeine Pruefung failed ODER Prior-Link skipped -> `needs_review = True` bleibt, `consistency_errors` dokumentiert den Grund (kein silent-fallback).

### 17.4 Cross-Account Kreditkarten-Settlement (unterstuetzt)

- `relink_all_transfers(max_days=5)`: Auto-Erkennung fuer unverlinkte interne Geldbewegungen;
  beide Formen: klassisches Tx-Paar (-X/+X) und statement-basierte Kreditkarten-Ausgleichsbuchungen
  (z. B. Bank-Belastung "LADUNG KREDITKARTENKONTO 1000.00" + Kartenauszug mit Endsaldo -1000.00).
  Ergebnis: `statement_settlements`-Link + `transaction_nature = 'settlement'`.
- `detect_statement_settlement_gaps(max_days_after_statement=45, extended_search_days=180)`:
  Read-only-Diagnose offener Faelle: `no_candidate`, `candidate_out_of_window`,
  `ambiguous_in_window`, `single_candidate_in_window` (Kandidatenlisten, max. 10).
- Grenzen: Zuordnung nur im produktiven Fenster (Default 5 Tage); Betraege muessen exakt in Cents
  uebereinstimmen; Belastungen **auf** dem Kartenkonto selbst werden nicht verlinkt.
  Restfaelle (z. B. Kartensaldo -9.61 ohne passende 9.61er-Belastung) bleiben konservativ offen
  und werden per Gap-Diagnose klassifiziert statt still zu verlinken.

### 17.5 Test-Abdeckung (2026-09-09)

- `tests/test_finance_prior_balance.py` -- 14 Tests (Lookup-Semantik auf Einheitenebene).
- `tests/test_finance_prior_balance_real_data.py` -- 18 Tests: reale Auszugs-Ketten
  (Bank/Karte), 1-Cent-Abweichung, fehlender/falscher Vorzeitraum, Repair-Pfad
  (Fake-Docling + gepatchter `_extract_header`), Cross-Account-Settlement (Link, Nature-Update,
  Fenster-Limits, Gap-Klassifikation, Idempotenz).
- Alle CPU-only: kein LLM, kein Embedding-Modell im VRAM (Statements via oeffentlicher API
  ohne Transaktionen; Buchungen via rohem SQL; Repair via Fake-Docling).
- Verwandschafts-Suiten `tests/test_finance_consistency.py`, `tests/test_finance_db_consistency.py`,
  `tests/test_finance_consistency_db.py`: insgesamt 85 Tests bestanden.

---

## 18. SOTA Phase 1 – Monarch-Core Prognosen (2026-09-12)

**Deterministische Cashflow- und Guthaben-Prognose für den Finance-Tab.**
Schedule-first-Hybrid: wiederkehrende Zahlungen als deterministischer Plan +
variable Einnahmen/Ausgaben via OLS-Trend × Kalendermonats-Saisonalität +
Residual-Bootstrap-Konfidenzintervall; Guthaben wird vom letzten
`effective_balance_at` fortgeschrieben. Kein ML, kein LLM, keine neuen
Dependencies, kein Future-Leak.

### 18.1 Neue Tools (`finance/tools.py`)

| Tool | Zweck | Parameter |
|------|-------|-----------|
| `finance_upcoming_bills` | Fälligkeits-Kalender: nächste Fälligkeit, Betrag, Abo-Kennzeichnung im Vorwärtsfenster | `days_ahead` 1–180 (Default 30), `iban`, `reference_date` |
| `finance_cash_flow_forecast` | Monats-Nachweis (Einnahmen / Wiederkehrend / Variable Ausgaben / Netto) mit KI + Guthaben-Kurve | `forecast_months` 1–24 (6), `lookback_months` 3–36 (12), `confidence_level` 0.5–0.99 (0.8), `include_balance` (True), `iban`, `reference_date` |
| `finance_subscription_audit` | Abo-/Recurring-Audit: Monats-/Jahreskosten, Trend, letzte Preisänderung, Abo-Heuristik | `iban`, `reference_date` |

### 18.2 Methodik (Schedule-first-Hybrid)

- **Deterministischer Anteil:** wiederkehrende Ausgabengruppen (≥ 2 Buchungen,
  `_recurring_groups`) werden mit ihrem Historien-Monatswert und dem nächsten
  Fälligkeitstag projiziert (Anchortag = Median der letzten 5 Buchungstage,
  `_next_due_on_or_after`).
- **Stochastischer Anteil:** variable Einnahmen/Ausgaben (Gesamt minus
  Wiederkehrendes) je Monat; Trend per OLS (`_fit_trend`), Saisonalität über
  Jahres-Monats-Indizes (`_seasonal_index`, Degradation auf Index=1 bei
  < 12 Monaten Historie).
- **Unsicherheit:** Residual-Bootstrap (`_bootstrap_interval`, B=1000, fester
  Seed → deterministisch, Perzentil-Intervall); CI-Reihenfolge-Invariante
  (untere ≤ Punkt ≤ obere); leere Residuals → Punktintervall.
- **Guthaben-Kurve:** Start = letztes `effective_balance_at` (Bankwahrheit
  minus verlinkte interne Transfers); nur bei IBAN + Einzelwährung, sonst
  `balance: null` (konsistent mit dem `estimated_savings`-Pattern).
- **Ausschlüsse / Invarianten:** Transfers via bestehende
  `_non_transfer_clause`; Währungen getrennt (keine Kursumrechnung — keine
  lokalen Kursdaten); nur Fakten bis `reference_date` (`_facts_up_to`,
  kein Future-Leak); Preisänderungen via `_detect_price_change`.

### 18.3 Registrierung (Single Source of Truth)

- `agent/tool_schemas.py` +3 OpenAI-Schemata · `agent_toolkit.py` +3
  Dispatch-Wrapper (read-only, Fail-Fast-Validatoren bei Import)
- `agent/tool_profiles.py`: `FINANCE_ANALYTICS` +3
- `finance/chat.py`: Planner-Prompt +3 Routings, Reflector +3 Actions,
  Retry-Dispatch +3 · `finance/query_reflector.py` + `finance/grammar_compiler.py`:
  `_REFLECTOR_ACTIONS` / `FINANCE_TOOL_NAMES` + `REFLECTOR_ACTIONS` +3
- `finance/tab.py`: neuer Sub-Tab „📈 Prognosen" (`_render_forecast_tab`):
  Konto-Filter + Slider (Horizont 1–24, Rückblick 3–36, Konfidenz 0.5–0.99,
  Guthaben-Checkbox, Fälligkeitsfenster 7–180), Monats-Tabelle, Plotly-Chart
  (KI-Band + Guthaben), Fälligkeits-Tabelle, Audit-Tabelle
- i18n: `finance_ui.forecast.*` (43 Keys) + `finance_ui.tabs.forecast` (DE/EN/BG)

### 18.4 Test-Abdeckung (2026-09-12)

- `tests/test_finance_monarch_core.py` — 26 Tests: saisonales Residual-Verhalten,
  Balance-Ketten-Korrektheit, CI-Reihenfolge, kein Future-Leakage, Transfer-Ausschluss,
  Multi-Currency, Bills-Anker-/Fenster-Logik, Audit-Klassifikation und -Totale.
  Alle CPU-only, deterministisch (feste Bootstrap-Seed, injizierbare Referenzdaten).
- Breitere Suite (2026-09-12): `test_finance_analytics_tools`, `test_finance_chat`,
  `test_finance_structured_runtime`, `test_finance_tab_regressions`,
  `test_i18n_consistency`, `test_tool_profiles` — **220/220 PASS** (2026-09-12).

### 18.5 Grenzen & Next Steps

- **Grenzen Phase 1:** keine ML-Forecasts (ARIMA/Prophet/XGBoost — 12–36
  Monatspunkte = Overfit-Risiko, schwere Dependencies, Non-Determinismus,
  AGPL-Lizenzcheck), keine LLM-Generierung (Arithmetik unzuverlässig, nicht
  reproduzierbar), keine externen APIs/Cloud. Bewertung der Varianten in 5
  Kategorien × 1–7: `docs_archive/finance_sota_phase1_workdoc_20260912.md`.
- **Phase 2 (abgeschlossen 2026-09-15):** Goals/Sinking Funds mit SQLite-Tabellen;
  UI-Tab „🎯 Sparziele" (Anlage, Status, Projektion, Zuordnung, Kandidaten —
  `finance/tab.py::_render_goals_tab`), 77 i18n-Keys DE/EN/BG, +47 Tab-Regressionstests.
  Verifizierte DAO-/Tool-Vertraege und Tests siehe §19. Deklarierter Rest (in
  Phase 3 uebernommen): kein automatischer Stopp am Zielbetrag/-termin im
  Prognose-Overlay (siehe oben). Doku: funktionen.md („Sparziele-Tab"-Sektion);
  Workdoc: docs_archive/WORKDOC_FINANCE_SOTA_PHASE2.md.
- **Phase 3 (offen):** Szenario-What-If-Engine, ML-Experimente,
  Anomalie-Erkennung 2.0.
- Bootstrap-KI bei sehr kurzer Historie (< 12 Monate) ist grob — wird per
  `notes` deklariert, nicht versteckt.

---

## 19. Sparziele: Verifizierte Vertraege (2026-09-15)

- `FinanceDB.upsert_goal`: Ohne explizite Waehrung greift die bestehende
  IBAN-Ableitung (DE/AT: EUR, CH: CHF, sonst `DEFAULT_CURRENCY`). Explizite
  Waehrungen bleiben vorrangig; dies ist keine Waehrungsumrechnung.
- `goal_contributions`: `UNIQUE(transaction_id)` erzwingt hoechstens ein
  Ziel pro Buchung; beide Fremdschluessel verwenden `ON DELETE CASCADE`.
  Tests pruefen SQLite-Metadaten statt eine bestimmte SQL-Schreibweise.
- `FinanceTools.project_goal`: Fortschritt unter `progress.saved_cents`;
  Rate explizit > geplant > Historie. Auch die historische Rate ignoriert
  Buchungen nach dem Referenztag, einschliesslich desselben Monats.
  Die Monatsserie beginnt im Folgemonat. Bereits erreichte Ziele haben
  `months_left_at_rate=0` und den Referenzmonat als `achieved_month`
  (keine Rekonstruktion des historischen Erreichungsdatums).
  `months_until_target_date` liefert die vorzeichenbehaftete
  Kalendermonatsdifferenz auch bei vergangenen Zielterminen.
- `suggest_goal_candidates`: Stabile wiederkehrende **Ausgaben** mit
  Periode >45 Tage, ohne Zielzuordnung; `min_occurrences >= 2` (Default 3).
  `reference_date` ist optional (Default heute); kein `window_days`.
  Monatsbetrag unter `monthly_equivalent`; keine Summe ueber Waehrungen.
- `cash_flow_forecast`: Horizont per `forecast_months`, Monatsreihen unter
  `results[*].months`. Optionales `goals`-Overlay mit `count`, `goals` und
  `monthly_draw_by_currency`; `balance_with_goals` beruecksichtigt die
  kumulierten Raten. Ein Kontostand setzt IBAN und Einzelwaehrung voraus.
  Der aktuelle Overlay-Vertrag zieht positive geplante Raten von Zielen
  mit Status `active` ab; ein automatischer Stopp am Zielbetrag/-termin
  ist damit noch nicht implementiert.
- Registrierung: `get_tool_schemas()` und `get_available_tool_schemas("finance_tab")`.
  `FINANCE_ALL` enthaelt alle zehn Goal-Tools; das ReAct-Profil `finance_tab`
  enthaelt nur deren fuenf Lese-Tools, keine Schreib-Tools.

**Teststand:** `tests/test_finance_goals_schema.py`: 43 bestanden.
Gemeinsamer Lauf mit Monarch-Core, Analytics, drei DB-Konsistenz-Suiten,
Finance-Chat, Structured Runtime, Tab-Regressionsfaellen und beiden
Tool-Profil-Suiten: **211 bestanden** im Projekt-venv. Keine produktiven
Daten oder LLM-/GPU-Laeufe; kein Gesamtprojekt-Release-Gate.

---

*Für Änderungen am Finance-Modul, dieses Dokument aktualisieren.*