<!-- last-verified: 2026-09-24 -->
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
| Tools | `finance/tools.py` | SQLite-Abfragen und deterministische Analysen | 64 exponierte Tools implementiert (FINANCE_ALL; Partitions-SSoT: `agent/tool_profiles.py`) |
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
Residual-Bootstrap-Konfidenzintervall; Guthaben wird vom tatsaechlichen
`balance_at` am Referenztag fortgeschrieben. Kein ML, kein LLM, keine neuen
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
- **Guthaben-Kurve:** Start = `balance_at` am Referenztag (Bankwahrheit,
  einschliesslich der tatsaechlichen Wirkung interner Transfers); nur bei IBAN + Einzelwährung, sonst
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
  Phase 3 uebernommen): Szenario-What-If-Engine. Das Prognose-Overlay begrenzt
  Ziehungen bereits durch Restbetrag und Zieltermin. Doku: funktionen.md („Sparziele-Tab"-Sektion);
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
  mit Status `active` ab; Ziehungen enden beim Restbetrag bzw. ab dem
  Folgemonat des Zieltermins.
- Registrierung: `get_tool_schemas()` und `get_available_tool_schemas("finance_tab")`.
  `FINANCE_ALL` enthaelt alle zehn Goal-Tools; das ReAct-Profil `finance_tab`
  enthaelt nur deren fuenf Lese-Tools, keine Schreib-Tools.

**Teststand:** `tests/test_finance_goals_schema.py`: 43 bestanden.
Gemeinsamer Lauf mit Monarch-Core, Analytics, drei DB-Konsistenz-Suiten,
Finance-Chat, Structured Runtime, Tab-Regressionsfaellen und beiden
Tool-Profil-Suiten: **211 bestanden** im Projekt-venv. Keine produktiven
Daten oder LLM-/GPU-Laeufe; kein Gesamtprojekt-Release-Gate.

---

## 20. Finance-Tab: Korrekturen und Grenzen (2026-09-16)

- **Budgets:** Positive Limits und positive Ist-Ausgaben. Negative Altwerte
  werden beim Lesen normalisiert; keine Umschreibung produktiver Daten.
  Das bestehende Budgetmodell hat keine Waehrungsspalte: explizit
  `DEFAULT_CURRENCY` (CHF), keine Umrechnung oder Umdeutung historischer
  EUR-Beschriftungen. Anderslautende historische Budgetabsichten muessen
  manuell geklaert werden. Fremdwaehrungsbuchungen werden ausgeschlossen.
- **Kategorien:** `upsert_category(overwrite_kind=False)` erhaelt den Typ bei
  UI-/Chat-Zuweisung und beim Budgetsetzen. Explizite Kategorienverwaltung
  kann den Typ weiterhin aendern. Erstattungen verwandeln Ausgabenkategorien
  nicht mehr in Einnahmekategorien. Bereits falsch gesetzte Typen werden nicht
  pauschal migriert.
- **Waehrungen:** `aggregate` gruppiert immer auch nach Waehrung und hat einen
  optionalen `currency`-Filter. UI-Auswertungen waehlen eine Waehrung;
  `monthly_report` lehnt gemischte Summen ohne explizite Waehrung ab.
  Kontofilter gelten auch fuer Budget-Istwerte; Limits bleiben Haushaltslimits.
  Fremdwaehrungsreports enthalten keinen CHF-Budgetvergleich.
- **Prognose:** Reales Einzelkontoguthaben als Startwert; Transfers bleiben
  aus dem Cashflow-Fit ausgeschlossen, nicht aus dem Bankstand. Weiterhin
  Monatsmodell ab dem Folgemonat, keine tagesgenaue Restmonats- oder geplante
  Einzelkonto-Transferprognose. `effective_balance_at` bleibt als Legacy-API
  erhalten, ist aber kein realer Konto- oder Haushaltsgesamtstand.
- **Sparziele:** Vorschlaege werden kontogebunden im Session-State gehalten;
  Uebernahme funktioniert beim naechsten Klick. Auswahl ueber Transaktions-ID,
  Ausschluss von Buchungen aller bereits belegten Ziele. Anlage uebernimmt
  Kontowaehrung, Kandidaten ihre Buchungswaehrung; Tabellen zeigen Waehrungen
  explizit statt festem Euro-Symbol. Reports und angeforderte Projektionen
  ueberstehen fachfremde Reruns.
- **Weitere UI-Korrekturen:** Kalendergueltige Monatseingaben, Datumsbereichs-
  Validierung, gespeicherter Kontotyp als Auswahlvorgabe; DE/EN/BG-Vertraege
  und Planner-Toolschemas angepasst.

**Verifikation:** Breite synthetische Finance-/i18n-/Toolprofil-Suite:
344 bestanden. Danach gezielte UI-/Goal-Pruefung: 110 bestanden;
abschliessende Analytics-Pruefung: 14 bestanden. AppTest prueft echte
Streamlit-Klickfolgen. Produktive DBs, LLM und GPU wurden nicht verwendet;
kein Live-App-/Modelltest und kein Gesamtprojekt-Release-Gate.

---

## 21. Serien: Tools-API (2026-09-17)

Wiederkehrende Zahlungen als erste-Klasse-Modell: `series`,
`series_candidates`, `series_exceptions` (skip/move/amount + `note`) und
`series_source_links` (AP2 Stage 1; Workdoc
`docs/FORECAST_UX_WORKDOC_2026-09-16.md`).

**Engine:** `finance/series_engine.py` — `estimate_cadence` (Tage/Wochen/
Monate-Multiplen), `clamp_day` (Anker 29-31 => Monatsende), `expand_series`
(deterministisch, Cap 2000), `detect_candidates` (2+ Beobachtungen =>
pruefbarer Kandidat, NIE auto-confirmed; Gruppierung nach IBAN/Currency/
Direction/Counterparty).

**DAO:** `finance/db_schema.py` — CRUD mit `revision`-Optimistic-Locking,
Kandidaten-Lifecycle (pending/confirmed/rejected), Ausnahmen pro
(series_id, original_due_date, typ), Journal + `undo_series_change`,
`note`-Migration, `evidence_json` NOT-NULL (leerer String als Default).

**Tools (`finance/tools.py`, alle `{"success": bool, ...}`):**

| Tool | R/W | Verhalten |
|------|-----|-----------|
| `finance_list_series` | R | Filter status/iban/direction/source |
| `finance_list_series_candidates` | R | Filter status |
| `finance_series_calendar` | R | Expansion + Ausnahmen (skip/move/amount) im Fenster |
| `finance_detect_series_candidates` | W | Facts => Kandidaten (pending) |
| `finance_confirm_candidate` | W | optionaler Korrekturensatz (name/cadence/amount/start) |
| `finance_reject_candidate` | W | status => rejected |
| `finance_pause_series` / `finance_resume_series` / `finance_end_series` | W | Statuswechsel (paused/active/ended) |
| `finance_skip_occurrence` / `finance_move_occurrence` / `finance_change_occurrence_amount` | W | Ausnahmen mit optionaler `note` |

Konventionen: Beträge als Float, intern Cents-INTEGER
(`_to_cents(float(amount))`; Fehler => `invalid_param`); ISO-Daten;
DAO-`ValueError` => `error_class` `not_found`/`conflict`/`invalid_param`.
Kandidaten bleiben `pending` — die Bestätigung ist immer eine explizite
Nutzerentscheidung.

**Registrierung:** 12 Schemas in `agent/tool_schemas.py`; 12
Dispatch-Einträge + 12 Wrapper in `agent_toolkit.py`; Profile in
`agent/tool_profiles.py`: `FINANCE_ANALYTICS` +3 Read-Tools,
`FINANCE_WRITE_TOOLS` +9 Write-Tools (FINANCE_CORE/ALL bleiben
unangetastet).

**Verifikation (2026-09-17):** `tests/test_finance_series_tools.py`
47/47; DAO + Engine 118/118; Tools/Goals-Schema/Profile-Gating 136/136;
breite Finance-/Tool-/Schema-Suite 516/516 im Projekt-venv; `py_compile`
auf allen geänderten Dateien. Keine LLM-/GPU-Läufe, keine produktiven
Daten.

---

## 22. Serien: Forecast-UX Stage 3 (2026-09-18)

Die Serien-/Kandidaten-API aus §21 ist jetzt im Finance-Tab
(Forecast-Sektion, `finance/tab.py` → `_render_forecast_series_section`)
nutzbar — deterministisch, ohne LLM, ausschließlich über die kanonischen
FinanceTools (keine DAO-Schreibzugriffe im UI-Pfad):

| UI-Baustein | Verhalten | Kanonisches Tool |
|-------------|-----------|------------------|
| Erkennungs-Button | deterministische Kandidatenerkennung; Flash mit `{count}`/`{new}` + `st.rerun()` | `detect_series_candidates` (IBAN-Filter) |
| Kandidaten-Expander | nur `pending`; Gegenpartei/Turnus/Betrag/Art/Konfidenz/Beobachtungen; Bestätigen/Ablehnen | `confirm_candidate` / `reject_candidate` (mit `fingerprint`) |
| Serien-Tabelle | Gegenpartei, Turnus, Betrag, Art, Status, Quelle, Gültigkeitsfenster | `list_series` (IBAN-Filter) |
| Statusaktionen | active → Pausieren/Beenden; paused → Fortsetzen/Beenden; ended → Hinweis, keine Aktionen | `pause_series` / `resume_series` / `end_series` (mit `series_id`) |
| Fehlerpfad | `success=False` → `st.error` mit `{error}`, kein Crash | — |

i18n: 35 neue Keys `finance_ui.forecast.series*` in DE/EN/BG
(nach `plan_due_required`); Labels über `_tr` mit Default-Fallback.
Helfer: `_series_status_label`, `_series_source_label`,
`_series_cadence_label`, `_series_amount_label` (reine Funktionen;
unbekannte Werte → Rohwert bzw. `–`).

**Verifikation (2026-09-18, Projekt-venv `venv_bot_20260802`):**
`tests/test_finance_forecast_ux_stage3.py` **30/30** (AppTest mit
deterministischem Tools-Stub; inkl. i18n-Konsistenz über
`tests/test_i18n_consistency.py`); Finance-Regression 192/192;
Voll-Suite `tests/` **1721/1721 PASS** (Exit 0); `py_compile` +
Locale-JSON-Validierung OK; `scripts/check_licenses.py` OK.
Kein LLM-Load, keine produktiven Daten. Details + Gate-Log: Workdoc
`docs/FORECAST_UX_WORKDOC_2026-09-16.md` (Abschnitt S3).

## 23. Serien: Forecast-UX Stage 4 / AP3 (2026-09-19)

Forecast-only-Support für wiederkehrende Verbindlichkeiten: **geschätzte Cadence**
(deterministische Schätzung ohne Fallback), **reversible Prognose-Unterdrückung**
(berührt weder Buchungen noch bestätigte Serien) und **manuelle Serien**
(anlegen/aktualisieren mit Optimistic Locking).

### 23.1 Geschätzte Cadence (`estimate_cadence`, `finance/series_engine.py`)

`upcoming_bills()` und `subscription_audit()` tragen je Ausgabengruppe:

| Feld | Bedeutung |
|------|-----------|
| `estimated_cadence` | `monthly`/`n_months`/`yearly`/`weekly`/`n_weeks` oder `null` |
| `estimated_period_n` | Perioden-Multiplikator (1 bei monthly/yearly) |
| `estimated_anchor` | erste Beobachtung als Ankerdatum |
| `estimated_next_due` | nächste geschätzte Fälligkeit (Monatsgitter für monthly-artig, Wochenschritt für weekly-artig) |
| `estimated_monthly` / `estimated_annual` | Cadence-Äquivalente (nur bei bekanntem Betrag) |
| `estimated_confidence` | `low` (2 Beobachtungen) / `medium` (3+) |
| `cadence_source` | **Drei-Zustands-Signal**: `"series"` (bestätigte Serie), `"estimated"` (geschätzt), sonst `null` |

Konservative Regeln: < 2 Beobachtungen oder eindeutiger Rhythmus nicht
bestimmbar → alle Felder `null`. Kein Fallback auf „monthly", keine
Phantom-Projektion. Bestätigte Serien haben Priorität
(`cadence_source = "series"`), auch wenn eine Schätzung existiert.

### 23.2 Reversible Prognose-Unterdrückung (forecast-only)

Neue Tabelle `forecast_suppressions` (`db_schema.py`): `iban`, `counterparty`
(`UNIQUE (iban, counterparty)`), `currency`, `reason`, `active (0/1)`,
Zeitstempel; Index auf `(active, iban)`.

| Tool | Art | Verhalten |
|------|-----|-----------|
| `suppress_forecast` | Write | Pflicht `iban` + `counterparty`; optional `currency`/`reason`. Idempotent: bestehende Zeile (auch inaktiv) wird zurückgesetzt und re-aktiviert |
| `restore_forecast` | Write | Zeile auf `active=0` (Historie bleibt); ohne Treffer → `error_class="not_found"` |
| `list_forecast_suppressions` | Read | aktive Zeilen; `include_inactive=true` zeigt auch wieder aktivierte; optional IBAN-Filter |

Scope: (iban, normalisierte Gegenpartei, Währung). Die Filterung wirkt in
`upcoming_bills()` (Bills + Window-Okkurrenzen; zusätzlich `suppressed`-Liste)
und `subscription_audit()` (Groups; zusätzlich `suppressed`-Liste).
**Buchungen und bestätigte Serien bleiben unverändert** (testabgesichert).

### 23.3 Manuelle Serien

| Tool | Pflicht | Verhalten |
|------|---------|-----------|
| `create_manual_series` | `iban`, `amount` > 0, `cadence`, `anchor_date` | legt eine bestätigte Serie an (status `active`, `direction=expense`), **erzeugt KEINE Buchungen**; optional `counterparty`, `title`, `currency`, `period_n`, `anchor_day`, `effective_from`, `effective_to` |
| `update_manual_series` | `series_id` + mind. ein Feld | änderbar: `amount`, `cadence`, `period_n`, `anchor_date`, `anchor_day`, `counterparty`, `title`, `currency`, `direction`, `effective_*`; Optimistic Locking via `expected_revision` (Default: aktuelle Revision; Stale → `error_class="conflict"` mit Hinweis zum Neuladen) |

`cadence` ∈ {monthly, n_months, weekly, n_weeks, yearly}; `amount` ist der
Betrag pro Periode (intern Cents).

### 23.4 Agent, UI, i18n

- **Agent**: alle fünf Tools registriert — `agent_toolkit.py` (Mapping
  `finance_*` → Wrapper), `agent/tool_schemas.py` (Schemas mit
  „WANN VERWENDEN"-Hinweisen); Profile: `suppress_forecast`,
  `restore_forecast`, `create_manual_series`, `update_manual_series` in
  `FINANCE_WRITE_TOOLS` (nur Finance-Pipeline),
  `list_forecast_suppressions` in `FINANCE_ANALYTICS`.
- **UI** (`finance/tab.py`, Forecast-Sektion): „Kommende Fälligkeiten" und
  Audit-Tabelle zeigen Spalten *Turnus* + *Quelle* (bestätigt/geschätzt) und
  je Zeile einen „🚫 Aus Prognose"-Button; Sektion „Aus der Prognose entfernt
  (reversibel)" mit „↩️ Wiederherstellen"-Button pro Konto/Gegenpartei.
  Helfer: `_forecast_source_label`, `_suppress_action`, `_restore_action`,
  `_render_forecast_suppressed` (reine Funktionen; nur kanonische Tools).
- **i18n**: neue Keys `finance_ui.forecast.bills_col_source`,
  `bills_source_series`, `bills_source_estimated`, `suppress_btn`,
  `suppress_help`, `suppress_ok`, `restore_btn`, `restore_ok`,
  `suppressed_title` in DE/EN/BG (Labels über `_tr` mit Default-Fallback).

### 23.5 Verifikation (2026-09-19, Projekt-venv `venv_bot_20260802`)

- `tests/test_finance_forecast_ux_stage4.py` **39/39 PASS** — geschätzte
  Cadence (konfident/unsicher/irregular, Priority über Serien), Suppression
  (Exclusion in beiden Forecast-Tools, Buchungen unverändert, Idempotenz,
  Restore ohne Löschung, Scope-Isolation zwischen Konten, bestätigte Serien
  unangetastet), `create_manual_series` (Happy Path, keine Buchungen,
  Projektion im Fenster, 8 Invalid-Param-Fälle), `update_manual_series`
  (Revision-Inkrement, Cadence-Wechsel, Stale-Revision-Konflikt, 5
  Invalid-Param-Fälle, `not_found`) — alle auf temporären SQLite-DBs,
  keine produktiven Daten.
- Regression: Stage-2 + Stage-3 + Series-Tools + Series-DAO **151/151 PASS**.
- `py_compile` (`finance/tools.py`, Testdatei) OK; Locale-JSONs gültig;
  alle i18n-Keys in DE/EN/BG vorhanden.

---

*Für Änderungen am Finance-Modul, dieses Dokument aktualisieren.*