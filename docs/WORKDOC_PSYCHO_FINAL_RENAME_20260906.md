<!-- last-verified: 2026-09-06 -->
# Workdoc: Finale psycho→wellbeing-Umbenennung (Scope-C)

Status: IN ARBEIT · Priorität: P0 (Namen-Residuen) · Datum: 2026-09-06
Vorgänger: `docs/27_SCOPE_B_FULL_RENAME_WORKDOC.md` (2026-08-16/18)

## Ziel

Alle aktiven **Dateinamen**, **öffentlichen API-Namen** und **identitätsgebenden
Bezeichner** von `psycho*` auf `wellbeing*` umbenennen — konsistent und
nachvollziehbar. Datenformate (persistierte Werte) und geschützte
Rechtstexte bleiben dokumentiert erhalten.

## CoT (Kurzform)

1. Scope-B benannte fast alles um, ließ aber 16 Dateien mit `psycho*`-Namen
   (3 aktiv, 1 totes 0-Byte-File, 12 Tests) + ~400 aktive Bezeichner-Referenzen.
2. `git grep` zeigt: keine persistierten "psycho"-Strings außer Datenformaten
   (User-IDs `psych_<hash>`, `domain='psych'`, `source_type='psychology'`,
   Entity-Typ `psychological_concept`) — diese sind interne technische Werte,
   nie user-visible → Rechtsrelevanz: keine (siehe 26_RECHTSANALYSE).
3. Benennung ist Konsistenz-/Branding-Hygiene, nicht zusätzliche Rechtsabsicherung.
   User-visible Oberfläche war seit Scope-B bereits clean (i18n-Werte).

## ToT (Varianten)

- **V1** Dateien nur: 16 Renames, APIs bleiben `Psych*` → inkonsistent
- **V2** Dateien + API + Bezeichner + i18n-Keys (REKOMMENDIERT)
- **V3** V2 + Datenmigration (IDs/Domains) → hohes Risiko, null Rechtsnutzen
- **V4** V3 + Archive → zerstört Audit-Trail, null Nutzen

Kritik: CoT untergewichtete i18n-Keys und Session-State-Keys; ToT-V4 ist
unrealistisch (Archive = Beweismittel). → V2' = V2 + dokumentierte Residuen.

## 5-Kategorie-Bewertung (1–7, höher=besser; Migrationsrisiko: 7=minimal)

| Kategorie          | V1 | V2' | V3 | V4 |
|--------------------|----|-----|----|----|
| Korrektheit        | 6  | 7   | 5  | 4  |
| Robustheit         | 5  | 7   | 4  | 3  |
| Wartbarkeit        | 4  | 7   | 5  | 4  |
| Performance        | 6  | 6   | 6  | 5  |
| Migrationsrisiko   | 6  | 6   | 2  | 1  |
| **Σ**              | 27 | **33** | 26 | 17 |

## Rename-Map (Single Source of Truth)

### Dateien (git mv)
- `psychological_user_insight_extractor.py` → `wellbeing_user_insight_extractor.py`
- `utils/psychological_orchestrator_integration.py` → `utils/wellbeing_orchestrator_integration.py`
- `scripts/psychological_session_loader.py` → `scripts/wellbeing_session_loader.py`
- `wellbeing/psychological_interface.py` → DELETE (0 Bytes, tote Referenz)
- 12× `tests/test_psych*.py` → `tests/test_wellbeing_*.py`
- `data/psychological_sessions.{db,key}` → `data/wellbeing_sessions.{db,key}`
- `data/psychological_support.db` → `data/wellbeing_store.db`

### Symbole
- `PsychologicalUserInsightExtractor` → `WellbeingUserInsightExtractor`
- `PsychologicalSessionLoader` → `WellbeingSessionLoader`
- `PsychologicalContextBuilder` → `WellbeingContextBuilder`
- `PsychSessionState` → `WellbeingSessionState`
- `PsychoKGFAISSManager` → `WellbeingKGFAISSManager`
- `PsychoRAGBootstrapper` → `WellbeingRAGBootstrapper`
- `psychological_chat` → `wellbeing_chat`
- `handle_psychological_message` → `handle_wellbeing_message`
- `handle_psychological_chat_input` → `handle_wellbeing_chat_input`
- `generate_psychological_response` → `generate_wellbeing_response`
- `set/get_psychological_context_enabled` → `set/get_wellbeing_context_enabled`
- `integrate_psychological_orchestrator` → `integrate_wellbeing_orchestrator`
- `is/enable/disable_psychological_support` → `*_wellbeing_support`
- `get_psychological_status` → `get_wellbeing_status`
- `was_last_message_psychologisch_checked` → `was_last_message_wellbeing_checked`
- `get_psych_sessions_path` → `get_wellbeing_sessions_path`
- `self.psychological_interface` → `self.wellbeing_support_interface`
- `psycho_interface` (chat_logic + st.session_state) → `wellbeing_interface`
- `st.session_state.psych_current_session` → `wellbeing_current_session`
- `st.session_state._psych_session_manager` → `_wellbeing_session_manager`
- `"psych_tab"` (Tool-Profile) → `"wellbeing_tab"`
- i18n: `gui.tabs.psychological`→`gui.tabs.wellbeing`, `gui.psychology.*`→`gui.wellbeing.*`,
  `prompts.psychological.*`→`prompts.wellbeing.*`

## BEHALTEN (dokumentierte Residuen, begründet)

1. **Datenformate (persistiert):** User-IDs `psych_<hash>` (privacy_handler.py:160 +
   Guards llm_knowledge_graph), `domain='psych'` (RAG), `source_type='psychology'`
   (KG), Entity-Typ `psychological_concept` (KG + i18n-Key als Daten-Mapping)
2. **Migration-Konstanten:** `wellbeing/file_migration.py`, `schema_migration.py`
   (alten Namen als Migrationsquelle)
3. **PII-Keywords:** `psychologie`, `psychiatrie` (Detektion von Nutzerinhalt)
4. **Schützende Rechtstexte:** "ohne Psychotherapie/Diagnostik vorzutäuschen"
   (response_generator.py:118) — aktive Distanzierung, nicht Positionierung
5. **Erkennungs-Keywords:** agent_chatbot_logic.py:3272 (Nutzerwortschatz)
6. **LLM-Prompts über Nutzerwelt:** "psychologische Unterstützung benötigt"
   (beschreibt Nutzeranfrage, nicht App-Identität)
7. **Archive/Beweise:** docs_archive/, archive/, 26_RECHTSANALYSE, 27_SCOPE_B
   (historische Audit-Trail)

## Phasen

- A: Checkpoint (Tag), Backups, Workdoc
- B: 16 Datei-Renames (git mv) + Daten-Orphans + 0-Byte-Delete
- C: Inhalte je Datei (Consumer zuerst, dann Provider, dann Kommentare)
- D: Gates (py_compile, pytest, grep-Gates, i18n-Parität, Lizenzen)
- E: funktionen.md, Workdoc → docs_archive/, Commit, Report

## Gates

- `git ls-files | Select-String psycho` → nur docs_archive + Beweise
- `git grep -i psycho -- '*.py'` → nur begründete Residuen (s.o.)
- pytest: 0 FAIL / 0 ERROR (vorher: 1547 passed, 9 failed, 69 xfailed)
- i18n-JSON gültig + Parität (de/en/bg)
