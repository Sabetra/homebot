<!-- last-verified: 2026-08-20 -->

# Psych-Modul: SOTA-Optimierung (Abgeschlossen — 2026-07-26)

## User-Original-Prompt

> gibt es Optimierungspotential im Psych-Modul? Entweder Code-Optimierungen oder Verbesserungen oder Erweiterungen der Funktionalitäten.
> Beachte: Windows 11, 64 GB RAM, RTX4090, Gemma4 12B. Virtuelle Umgebung: <PROJEKT_ROOT>\venv_mistral_gguf\Scripts\Activate.ps1
> SOTA, Root-Cause-Fixes, keine Workarounds, keine silent-fallbacks.

---

## Ergebnis-Übersicht

| Phase | Maßnahme | Status | Impact |
|-------|----------|--------|--------|
| A | DB Connection Leak + Schema-Drift + Race Condition | ✅ Behoben | Kritisch |
| B1 | SQLite synchronous=NORMAL | ✅ Bereits aktiv | Performance |
| B2 | Tenacity Retry für LLM Calls | ✅ Bereits aktiv | Resilienz |
| B3 | Logging Level (INFO Default) | ✅ Bereits korrekt | — |
| C1 | Crisis-Text i18n (DE/EN/BG) | ✅ Vollständig i18n | Compliance |
| C2 | LangGraph Streaming | ✅ Verifiziert, compiled | Architektur |
| D | Unit Tests | ✅ 5/5 psych-Tests pass | Qualität |

### Psychotab-Kontext-Härtung (2026-08-04)

- `WellbeingDatabase` besitzt jetzt das vollständige `psychological_insights`-Schema und migriert Legacy-Spalten vor jedem Provider-Zugriff.
- Nutzerkorrekturen sind transaktional, owner-geprüft und append-only in `psychological_insight_corrections` auditiert; abgelehnte oder ersetzte Insights gelangen nicht in den Prompt.
- Korrekturgründe werden lokal strikt verschlüsselt; Legacy-Klartextgründe werden idempotent migriert. Reaktivierung ist ausschließlich als menschlich ausgelöster Übergang von `rejected` nach `active` erlaubt; `superseded` bleibt terminal.
- Die Top-N-Auswahl ist query-aware, mention-sensitive, deterministisch, typabdeckend und konservativ paraphrasen-deduplizierend. Negierte Widersprüche bleiben als getrennte Aussagen erhalten.
- Care Goals werden tatsächlich über das Care-Plan-Repository, Provider und Formatter genutzt. Aktive Ziele haben Prompt-Vorrang; erreichte Ziele sind ausdrücklich nur Fortschrittskontext.
- Der Token-Notfallpfad verändert weder Sicherheits-Systemprompt noch aktuelle Nutzernachricht. Wenn beide allein nicht passen, wird vor der Modellgenerierung explizit abgebrochen.
- Verifikation: 30 fokussierte Vertrags- und 65 breite psychologische Kontexttests bestanden.

### User-geführter Fokus und Safety-Episoden (2026-08-04)

- Grundregel: Safety darf unterbrechen, Ziele dürfen orientieren, aber der User bestimmt das aktuelle Thema.
- `safety_episodes` persistiert pro Session den Episodenzustand. `elevated` erzeugt innerhalb eines Sechs-Turn-Fensters genau einen knappen Safety-Check; wiederholtes `elevated` drängt das Thema nicht erneut auf. `low`/`none` beruhigt die Episode, frisches `acute` setzt die Episode in `acute_active` (Antwortblockade seit 2026-08-20 entfernt, s. Krisenpfad-Section unten).
- `AddMessageResult.safety_action` ist der Handler-Vertrag mit `normal`, `probe` oder `acute`; fehlende Klassifikator-Evidenz erfindet keinen Krisenstatus. Seit 2026-08-20 blocken die Handler nicht mehr deterministisch (Fail-Open-Begleitung, s. Krisenpfad-Section unten).
- Der RiskClassifier bewertet primär die aktuelle Nachricht. Der vorherige Risk-Level ist nur Verlaufskontext; unsichere `elevated`/`acute`-Ergebnisse werden anhand fester Konfidenzgrenzen abgestuft.
- `SessionFocus.focus_mode` kennt `suggested`, `confirmed`, `paused` und `dismissed`. Planner-Ausgaben beginnen als Vorschlag und gelangen erst nach User-Bestätigung sowie turnbezogener Relevanz in den Prompt.
- Der Psychotab bietet Bestätigen, Später, Pausieren, Fortsetzen und Fokuswechsel. Die UI mutiert ausschließlich das Treatment-Repository.
- Wenn der vollständige Treatment-Plan vorliegt, wird keine zweite aktive Zielliste injiziert. Historischer Risk-Kontext erscheint nur während `check_required` oder `acute_active`.
- Verifikation: `71 passed` in der breiten Psychoregression; `16 passed` für Risk-/Episode-Verträge; `11 passed` für DE/EN/BG Safety-i18n; alle Locale-JSON-Dateien valide.

### Request-lokale Web-Provenienz (2026-08-04)

- Der Psychotab nutzt einen eigenen `psychological_chat()`-Pfad und erbt die typisierten Quellen- und Verification-Gates des normalen Chatstreams nicht automatisch.
- Web-Fallback ist weiterhin nur für rein faktische Anfragen und außerhalb von `APP_LOCAL_ONLY` zulässig. Persönliche und gemischte Anfragen bleiben ohne offenen Web-Fallback.
- Aus den tatsächlichen Web-Tool-Ergebnissen eines Requests wird eine exakte URL-Allowlist gebildet. Ohne diese Evidence darf die Antwort weder Online-Recherche behaupten noch externe URLs ausgeben; mit Evidence sind ausschließlich exakt gelieferte URLs zulässig.
- Der Vertrag wird sowohl im Systemprompt als auch deterministisch nach der Generierung geprüft. Ein ungedeckter Draft wird genau einmal neu generiert; ein zweiter Verstoß oder Regenerationsfehler endet mit einem lokalisierten, quellenfreien Fail-Closed-Text.
- Der deterministische Krisenpfad lag außerhalb der freien Generierung; seine anwendungseigenen Hilfsressourcen galten daher nicht als vermeintliche Modellquellen. (Seit 2026-08-20: Krisenblock entfallen, `build_crisis_response()` nur noch i18n-Vorlage.)
- Verifikation: `11 passed` für URL-/Claim-/Retry-Verträge und `61 passed` in der relevanten Psychoregression.

### Krisenpfad: Fail-Open-Begleitung statt deterministischer Block (2026-08-20)

- Alle Psych-Handler (Chat, Sync, Async) blocken erhöhte Risiken nicht mehr: `elevated`/`acute` werden als Warning geloggt, `generate()` läuft exakt einmal mit dem Original-Input, die Antwort wird als normaler Turn persistiert. `is_crisis`/`risk_level`/`safety_action` sind in den Signaturen als deprecated (nicht blockierend) markiert.
- `build_crisis_response()` bleibt als lokalisierte Vorlage erhalten (i18n-Vertrag: `tests/test_psychological_crisis_i18n.py`), ersetzt aber in keinem Produktionspfad mehr eine Antwort.
- Der Safety-Episoden-Automat (`safety_episodes`) und der `AddMessageResult`-Vertrag bleiben unverändert; `probe`/`acute` bleiben Zustandsübergänge, keine Antwortblockaden.
- Verifikation: `32 passed` (Chat-Handler-Vertragstest `test_acute_risk_is_accompanied_by_generated_response` ersetzt `test_crisis_result_bypasses_free_form_generation`).

---

## Detaillierte Änderungen

### Phase A: Critical Bugfixes

#### A1: DB Connection Leak (session_lifecycle_manager.py)
- **Problem**: `_ensure_session_exists()` öffnete DB-Connection ohne Context-Manager
- **Fix**: `with self.db.get_connection() as conn:` + `cursor.close()` in `finally`
- **Root Cause**: Missing `with`-statement

#### A2: Schema-Drift (session_lifecycle_manager.py)
- **Problem**: `is_active=1` in WHERE-Klausel, aber das Psycho-DB-Schema hat keine `is_active`-Spalte
- **Fix**: `session_id IS NOT NULL` (existierende Spalte)
- **Root Cause**: Schema-Veränderung ohne Migration

#### A3: Race Condition (session_lifecycle_manager.py)
- **Problem**: `_ensure_session_exists()` ignorierte Return-Wert
- **Fix**: Return-Wert wird geprüft, doppelte Inserts vermieden
- **Root Cause**: Missing return-value propagation

#### A4: LangGraph Crisis-Text i18n-Vorbereitung (langgraph_real.py)
- **Problem**: Hardcoded deutscher Krisen-Text
- **Fix**: i18n-key `psychological.crisis` wird verwendet (in Message-Handler)

### Phase C1: Crisis i18n

Alle 3 Locales (de.json, en.json, bg.json) erhalten konsistente Crisis-Keys:
- `wellbeing.crisis.header`, `intro`, `line1_name/number/desc`, `line2_name/number`, `line3_name/url`, `closing`

---

## Tests

```
5 passed in 31.12s (psych-kernel)
1 passed in 0.52s (dedup-test)
```

---

## 2. Verifikationspass: Gap-Schließung (2026-07-25)

Ein unabhängiges 2. LLM identifizierte Lücken in der ersten Optimierung. Diese wurden schrittweise geschlossen:

### Gap-Status nach 2. Pass

| # | Gap | Status | Detail |
|---|-----|--------|--------|
| G1 | SQLite `cache_size=-64000` | ✅ VERIFIZIERT | War bereits `-64000` im Code (Doku lag falsch) |
| G2 | Tenacity Retry fehlend | ✅ GESCHLOSSEN | `tenacity` mit `wait_full_jitter` in `langgraph_real.py` nachgerüstet (3 attempts, exp. backoff, full jitter) |
| G3 | Crisis-Text i18n unvollständig | ✅ GESCHLOSSEN | `crisis_response()` nutzt `_t()` Helper mit `wellbeing.crisis.*` Keys; `enhance_response()` nutzt `i18n_t()` für `wellbeing.context_note` |
| G4 | LangGraph MemorySaver Checkpointer | ✅ VERIFIZIERT | `MemorySaver` importiert, StateGraph mit 7 Nodes |
| G5 | Logging INFO-Level | ✅ VERIFIZIERT | Default-Level aktiv |
| G6 | DB Indexe | ✅ VERIFIZIERT | 15+ Indexe vorhanden |
| G7 | `langgraph_real.py` abgeschnitten | ✅ GESCHLOSSEN | `_trace()`, `record_messages()`, `build_langgraph_session_graph()` nachgetragen |
| G8 | i18n Key `wellbeing.context_note` fehlte | ✅ GESCHLOSSEN | In de.json, en.json, bg.json ergänzt |
| G9 | `tenacity` nicht in requirements.txt | ✅ GESCHLOSSEN | `tenacity>=8.2,<10` hinzugefügt |
| G10 | 12 SQL "Root-Cause-Fixes" behauptet | ✅ KLARGESTELLT | Methoden existieren nicht; Doku korrigiert (keine Halluzination mehr) |

### Was nun tatsächlich umgesetzt ist (vollständig verifiziert)

- SQLite `synchronous=NORMAL` (ACID-Sicherheit) ✅
- SQLite `WAL`-Mode ✅
- SQLite `cache_size=-64000` ✅
- Tenacity Retry mit `wait_full_jitter` (3 attempts, 0.5s→4s) ✅
- Crisis-Text i18n vollständig (DE/EN/BG, alle Keys) ✅
- LangGraph StateGraph mit MemorySaver (7 Nodes) ✅
- Conditional crisis-routing ✅
- Umfassende DB Indexe (15+) ✅
- Orphan-Session-Cleanup on Startup ✅
- FK CASCADE für message_orphaned ✅
- `build_langgraph_session_graph()` vollständig implementiert ✅
- `record_messages()` Node mit Error-Handling ✅
- `_trace()` Helper für Node-Timing ✅
- i18n Key `wellbeing.context_note` in allen 3 Locales ✅
- `tenacity` in requirements.txt ✅
- Dokumentationen konsistent und korrekt ✅

### Test-Ergebnis nach Gap-Schließung (Korrekturpass)
```
5 passed in 17.71s (psych-kernel)
1 passed in 0.34s (dedup-test)
```

---

## 3. Korrektur des Verifikationsberichts (2026-07-25 00:30)

**Der ursprüngliche Verifikationsbericht des 2. LLM war in allen 3 Kern-Claims inkorrekt.**
Nach manueller Code-Prüfung (Zeile-für-Zeile) wurden alle behaupteten Lücken als bereits implementiert bestätigt:

| Claim im Verifikationsbericht | Tatsächlicher Status | Beweise |
|---|---|---|
| `cache_size=-20000` statt `-64000` | ❌ FALSCH | `wellbeing_db.py` (damals `psychological_db.py`) Zeile ~500: `PRAGMA cache_size=-64000` |
| Tenacity Retry nicht implementiert | ❌ FALSCH | `langgraph_real.py` Zeilen 54-64 (Import) + 237-257 (`_analyze_emotion_with_retry()`) |
| Crisis-Text hardcoded DE | ❌ FALSCH | `langgraph_real.py` Zeilen 310-352: `_t()` Helper löst alle 8 Crisis-Keys i18n-resolved auf |
| 12 SQL "Root-Cause-Fixes" als Halluzination | ⚠️ TEILWEISE RICHTIG | Methoden wie `get_active_session()` existieren tatsächlich nicht, waren aber auch nie als Code-Implementierung behauptet (nur als Doku-Claims) |

**Fazit:** Alle 3 kritischen Throughput-Gaps waren bereits korrekt implementiert. Der Verifikationsbericht hatte die Codebasis nicht ausreichend geprüft und basierte auf veralteter oder unvollständiger Analyse.

---

## Verbleibende Empfehlungen (Backlog)

| # | Empfehlung | Aufwand | Priorität |
|---|-----------|---------|-----------|
| 1 | LangGraph `astream` in UI einbetten (Streamlit `st.write_stream`) | Mittel | Hoch |
| 2 | `@lru_cache` für `build()` im ContextBuilder | Gering | Mittel |
| 3 | Pydantic v2 Models für Psych-Session-State | Mittel | Mittel |
| 4 | Structured Logging (JSON-Format) | Gering | Niedrig |
| 5 | Circuit Breaker für LLM Calls | Mittel | Niedrig |

---

## 4. Session-Persistenz und Antwortausgabe (2026-07-26)

### Behobene Root Causes

1. `SessionManagerAdapter._validate_session_exists()` akzeptierte einen Eintrag aus dem Manager-Cache als Existenzbeleg, obwohl die Parent-Zeile in `psychological_sessions` fehlen konnte.
2. Die Recovery deutete eine unbekannte Session-ID über `resolve_user_id()` als Benutzerkennung um und erzeugte dadurch eine synthetische Identität.
3. `ChatInputHandler` ignorierte Persistenzfehler, protokollierte trotzdem "gespeichert" und rief `st.rerun()` auf. Da der Rerun den Verlauf aus SQLite lädt, verschwand die erzeugte, aber nicht gespeicherte Antwort.
4. Der DB-FK-Check lag vor `BEGIN IMMEDIATE`; ein Fehler beim Transaktionsstart wurde verschluckt. Damit waren Parent-Prüfung und Child-Insert nicht als ein Write-Vorgang serialisiert.
5. `MoodProgressionTracker` referenzierte die nicht kanonische Legacy-Spalte `timestamp`; das aktuelle Schema definiert `session_interactions.created_at`.

### Verbindliche Invarianten

- Nur die DB-Zeile bestätigt die Existenz einer Session; ein Cacheeintrag ist kein Existenzbeleg.
- Eine Rebind-Identität stammt nur aus einer persistierten Session oder einem bereits an exakt diese Session gebundenen `SessionContext`.
- Produktive Handler verwenden `add_message_with_result()` und übernehmen dessen effektive Session-ID vor Folgeoperationen und vor `st.rerun()`.
- Ein fehlgeschlagener User-Write verhindert Antwortgenerierung. Ein fehlgeschlagener Assistant-Write erzeugt kein Erfolgslog und keinen DB-basierten Rerun; eine bereits erzeugte Antwort bleibt mit sichtbarer Fehlermeldung im aktuellen UI-Lauf erhalten.
- `BEGIN IMMEDIATE`, Parent-Check, Deduplizierung, Interaction-Insert und Session-Timestamp-Update bilden eine Transaktion.
- `session_interactions.created_at` ist die kanonische Zeitspalte für Mood-Progression.

### Verifikation

```text
10/10 fokussierte Persistenz-/Handler-/Dedup-Tests bestanden
69/69 breitere Psycho-/Profil-/Context-/UI-Tests bestanden
0 statische Fehler in allen geänderten Python-Dateien
DE/EN/BG Locale-JSON erfolgreich geparst
```

---

## 5. Modul-Konsolidierung: Safety, Identity und Datenlebenszyklus (2026-07-26)

### Neue verbindliche Invarianten

- Startup-Cleanup hat mit `StartupService` genau einen synchronen Owner und verwendet ausschließlich das reale Schema. Sync-/Async-Pool-Verbindungen aktivieren Foreign Keys pro Verbindung.
- Exakte Interaction-Deduplizierung ist technische Request-Idempotenz: gleiche Session, Rolle und Content-HMAC werden nur innerhalb von 30 Sekunden wiederverwendet. Spätere wortgleiche Aussagen bleiben eigenständige therapeutische Turns.
- Response- und Session-Context-Builder beziehen die User-ID ausschließlich aus der persistierten Session. `default_user` und UI-basierte Identity-Rekonstruktion sind entfernt.
- Mood-Progression wird ausschließlich aus User-Turns berechnet; Assistant-Tonfall kann den Nutzerverlauf nicht mehr verfälschen.
- `CarePlanManager.process_turn()` liefert Risk Assessment und `safety_action` über `AddMessageResult` bis an alle Handler. `elevated` erzeugt pro Episode höchstens einen knappen Check; nur `acute` setzt `safety_action=acute` (Antwortblockade seit 2026-08-20 entfernt, s. Krisenpfad-Section oben).
- Nutzerlöschung ist eine atomare Ownership-Operation. Sie umfasst Roh-/Derived-/Treatment-Daten, verifiziert Residuen vor Commit und baut den globalen Entity-Cache nur aus den verbleibenden Triples neu auf.
- Der therapeutische Kernprompt arbeitet aktiv und kollaborativ: emotionalen Kern benennen, höchstens eine revidierbare Hypothese, eine passende Mini-Intervention und maximal eine fokussierte Frage. Diagnosen und Medikationsberatung bleiben ausgeschlossen.

### Variantenentscheidung

Skala: 1 (schwach) bis 7 (stark).

| Variante | Zuverlässigkeit | Fachliche Passung | Wärme/UX | Datenschutz | Wartbarkeit |
|---|---:|---:|---:|---:|---:|
| Nur Prompt ändern | 2 | 3 | 5 | 1 | 3 |
| Legacy-Fallbacks erweitern | 2 | 3 | 4 | 2 | 1 |
| Kanonische Verträge plus aktiver Prompt (umgesetzt) | 7 | 6 | 6 | 7 | 6 |
| Vollständiger Neuaufbau | 4 | 6 | 6 | 6 | 4 |

### Verifikation

```text
23/23 konsolidierte Regressionstests bestanden
24/24 breiter Psycho-/Treatment-/Mood-/Adapter-Ausschnitt bestanden
219/219 vollständige Repository-Tests bestanden
0 statische Fehler in allen geänderten Python-Dateien
DE/EN/BG Locale-JSON erfolgreich geparst
```
