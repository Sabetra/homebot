<!-- last-verified: 2026-07-27 -->
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
| Tools | `finance/tools.py` | SQLite-Abfragen und deterministische Analysen | 34 exponierte Tools implementiert |
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

*Für Änderungen am Finance-Modul, dieses Dokument aktualisieren.*