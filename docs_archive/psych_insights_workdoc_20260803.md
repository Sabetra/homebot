# Workdoc: Psychological User Insights Implementation

| Field | Value |
|-------|-------|
| **Created** | 2026-08-03 23:12 |
| **Author** | Senior Python Engineer (Cline) |
| **Status** | COMPLETED AND REVERIFIED (2026-08-04) |
| **Related Task** | Implement psychological user insights - canonical schema, correction lifecycle, explainable Top-8, dedup, token budget |

## 1. Ziel

Implementiere die psychologischen User-Insights vollständig, lokal, sicher, deterministisch und therapeutisch sinnvoll. Behebe die Root Causes der verifizierten Defekte.

## 2. Verifizierte Defekte (Root Causes)

| # | Defekt | Root Cause |
|---|--------|-----------|
| R1 | `_select_hybrid_top_n` dedupliziert nur per `value.casefold()` | Keine Unicode-Normalisierung, keine semantische Dedup |
| R2 | `user_query` wird in Auswahl nicht verwendet | Builder sortiert nur nach Confidence, ignoriert Query-Relevanz |
| R3 | `_BASELINE_CATEGORIES` ist `set` → nicht-deterministisch | Set hat keine garantierte Iterationsreihenfolge |
| R4 | Provider berechnet log-Bayes, Builder sortiert danach nur nach Confidence | Doppelter Ranking-Schritt verwirft log-Bayes-Effekt |
| R5 | `correction_status` wird vom Extractor migriert, nicht von PsychologicalDatabase | Schema-Owner ist nicht kanonisch |
| R6 | COALESCE schützt nicht vor fehlender SQLite-Spalte | Migration muss vor SELECT laufen |
| R7 | `emergency_trim_messages` auf 4 Runden begrenzt | Starres Limit, kein Fortschritt-basierter Abbruch |
| R8 | Reproduzierbarer Fall: 5370 Tokens bei Budget 600 | Trim-Reihenfolge suboptimal, keine Komponenten-Struktur |
| R9 | `agent_chatbot_logic` loggt Overflow, ruft LLM trotzdem auf | Kein harter Abbruch bei Overflow |

##

## 3. Umsetzung

| Bereich | Ergebnis |
|---------|----------|
| Insight-Auswahl | Query-aware Hybridscore, deterministische Baseline, Typabdeckung und konservative Paraphrasen-Deduplizierung |
| Schema | `PsychologicalDatabase` ist kanonischer Owner inklusive idempotenter Legacy-Migration |
| Korrekturen | Transaktionale Owner-Prüfung, Statusvalidierung, Replacement-Validierung und append-only Audit |
| Extractor | Delegiert Schema- und Korrekturverantwortung an die Datenbank |
| Therapeutic Goals | Bestehende End-to-End-Nutzung verifiziert; ACTIVE vor ACHIEVED, klare Prompt-Semantik |
| Token-Budget | Immutable Systemprompt/Userquery, Deep Copy, garantierter Fit oder expliziter Fehler vor Generation |

### Follow-up aus dem zweiten Workdoc

- Korrekturgründe werden in Insight-Zeile und Auditlog strikt verschlüsselt. Ein Verschlüsselungsfehler bricht die Mutation ab, statt Klartext zu persistieren.
- Die idempotente Legacy-Migration verschlüsselt bereits vorhandene Klartextgründe und backfillt Auswahl-/Korrekturspalten.
- Reaktivierung ist ein expliziter, auditierter Human-Übergang von `rejected` nach `active`; Systemextraktion darf nicht reaktivieren und `superseded` bleibt terminal.
- Negierte Widersprüche werden nicht als Paraphrase dedupliziert.
- Der Auswahlzeitpunkt ist für reproduzierbare Tests injizierbar; das produktive Verhalten nutzt weiterhin UTC-Now.

## 4. Abnahme der ursprünglichen 17 Kriterien

| Bereich | Ergebnis |
|---------|----------|
| Schema (1-3) | Frisch-, Legacy- und Doppelmigration, Spalten und Indizes getestet; Extractor delegiert an den DB-Owner |
| Lifecycle (4-6) | Reject, Supersede und Reactivate transaktional auditiert; Retrieval und Reextraktion schützen korrigierte Insights |
| Auswahl (7-13) | Hardlimit, stabile Reihenfolge, Query-Wechsel, Mentions, Typ-Recency, exakte/nahe Dubletten und Negationskonflikte getestet |
| Tokenbudget (14-16) | Fortschrittsbasiertes Trimming; immutable Safety-/Query-Vertrag; Fit oder Abbruch vor LLM-Call |
| Fehlervertrag (17) | Providerfehler werden in `UserContextResult.errors` erfasst |

Die im zweiten Workdoc vorgeschlagene additive Formel `logBayes + queryRelevance + typeRecency + correctionWeight` wurde nicht wörtlich übernommen. Der implementierte Score kombiniert Evidenz und Recency multiplikativ mit Query-Relevanz; korrigierte Insights werden vor dem Ranking vollständig ausgeschlossen. Das verhindert, dass ein hoher anderer Teilscore eine Nutzerkorrektur überstimmt.

## 5. Variantenentscheidung

Skala 1 (schwach) bis 7 (stark); beim Migrationsrisiko bedeutet 7 geringes Risiko.

| Variante | Korrektheit | Robustheit | Wartbarkeit | Performance | Migrationsrisiko |
|----------|------------:|-----------:|------------:|------------:|-----------------:|
| Nur Builder-Ranking ändern | 3 | 2 | 4 | 6 | 6 |
| Extractor bleibt Schema-Owner | 3 | 3 | 2 | 5 | 4 |
| Kanonischer DB-Owner plus fail-closed Lifecycle (umgesetzt) | 7 | 7 | 6 | 6 | 6 |

## 6. Risiko, Datenschutz und Rollback

| Risiko | Minderung |
|--------|-----------|
| Legacy-Schema fehlt neuen Providern | Idempotente Migration vor Provider-Zugriff |
| Korrektur wird durch Reextraktion aufgehoben | Statusgeschützte UPSERT-Klausel plus Regressionstest |
| Auditgrund enthält PII | Strikte lokale Fernet-Verschlüsselung und Klartextmigration |
| Optionaler Kontext verdrängt Safety | Immutable Kern und fail-closed Pre-Generation-Gate |

Rollback erfolgt dateibezogen aus dem timestamped Backup unter `backups/psych_insights_workdoc_followup_20260804_085122/`. Eine Schema-Rückmigration ist nicht erforderlich: zusätzliche Spalten und Auditzeilen sind rückwärtskompatibel; verschlüsselte Gründe bleiben mit dem bestehenden lokalen Schlüssel lesbar.

## 7. Verifikation

- Fokussierte Vertrags-Suites: `30 passed`.
- Alle psychologischen Tests plus Kontext-/Provider-/Token-Nachbarsuites: `65 passed in 16.78s`.
- VS-Code-Diagnostik: keine Fehler in den berührten Code- und Testdateien.
- Der bekannte, unabhängige Full-Suite-Baselinefehler im Finance-Embedding-Bereich wurde nicht verändert.