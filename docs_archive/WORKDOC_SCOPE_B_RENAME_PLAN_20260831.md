# Workdoc: Scope-B-Rename — Exekutions- & Rollback-Plan (Wellbeing-Repositionierung)

> **Erstellt:** 2026-08-31 · **Status:** `COMPLETED` (2026-09-01) — C1–C8 ausgeführt, verifiziert, finalisiert (Details §14)
> **Autor:** Cline (Agent) · **Reviewer:** Sabetra
> **Verwandt:** `WORKDOC_LEGAL_DSGVO_PSYCH_20260831.md` (Legal) · `WORKDOC_PUBLIC_LAUNCH_20260831.md` (Launch-Hygiene)
> **Rollback-Checkpunkt:** Tag `pre-scope-b-rename` + Branch `checkpoint/pre-scope-b-rename` → **`5f4282a4`**

---

## 0. Ziel & rechtliche Einordnung

**Warum Rename?** Klinische Nomenklatur (Tab „Psychologie", „Therapie", „Diagnose", „Behandlung") erzeugt
MDR/UWG/PsyG-Risiko in der **öffentlichen** Darstellung. Ziel: nicht-klinische Wellbeing-/Care-Positionierung.

| Ebene | Rechtlich relevant? | Begründung |
|-------|--------------------|------------|
| **User-facing Strings** (Tab-Label, i18n-Werte, Disclaimer, README) | **JA** | MDR/UWG/PsyG greifen auf die Darstellung gegenüber Nutzern |
| **Verbatim klinische Instrumente** (PHQ-9/GAD-7/C-SSRS Items) | **JA** (Lizenz/IP) | ✅ **bereits entfernt** (Schritt 1, Workdoc-Legal DoD #1) |
| **Interne Python-Bezeichner** (Klassen-/Dateinamen) | **NEIN** (nur Konsistenz/Branding) | `TherapeuticPipeline` etc. erscheint **nie** in UI/Marketing |
| **DB-Tabellen-/Dateinamen** | **NEIN** — aber **Datenkompatibilität!** | Umbenennung bricht Nutzerdaten + Migration nötig |

**Fazit:** Der **rechtliche Launch-Zielzustand** = Schritt 1 (Code, ✅) + **Phase A** (user-facing i18n/Doku).
Die **interne Paket-/Klassen-Umbenennung** (Phasen B–D) ist Konsistenz/Branding — wertvoll, aber **nicht
rechtlich zwingend** und mit deutlich höherem Bruchrisiko.

---

## 1. Scope-Entscheidungen

### 1.1 RENAMIERT werden (Python-/i18n-/UI-Ebene)
- Paket `psychological_support/` → `wellbeing/` · Submodul `treatment/` → `care_plans/`
- Dateien (s. §2) · Konservative Klassen-Liste (s. §3)
- i18n-Keys `psychological.*` → `wellbeing.*`, `gui.tabs.psychology` → `gui.tabs.wellbeing`
- `get_psychology_path()` → `get_wellbeing_path()` (Rückgabepfad **unverändert**)
- UI-Tab-Label **Wert**: „🧠 Psychologie" → „🌱 Wellbeing & Reflexion"

### 1.2 NICHT renamiert (Daten-/System-Kompatibilität — kritisch)
| Element | Wert | Grund |
|---------|------|-------|
| **DB-Dateiname** | `psychological_support.db` | Geteilt mit KG (`get_kg_path()` liefert dieselbe Datei); 14 Tabellen; Rename bricht Nutzerdaten |
| **14 DB-Tabellen** | `psychological_sessions`, `session_interactions`, `screening_results`, `alliance_scores`, `case_formulations`, `homework_tasks`, `cumulative_risk`, `outcome_assessments`, `context_summaries`, `psychological_insights`, `psychological_insight_corrections`, `knowledge_graph_entities`, `triples`, `kg_entities` | Existierende Nutzerdaten; Rename = Migration |
| **Session-Pfade / RAG-FAISS-Cache-Keys** | — | Daten-/Cache-Kompatibilität |
| **KG-Struktur** | `PsychologyKG` (RAG-Domain) | Geteiltes Domain-Konzept |

### 1.3 Reconciliation: Workdoc-Legal B-Liste vs. Realität (Korrektur)
| Item | Problem | Korrektur |
|------|---------|-----------|
| **B8** `TherapeuticCore` | Klasse `TherapeuticCore` **existiert nicht** | Echte Klasse: **`TherapeuticPipeline`** (19 Refs, 3 Dateien). Datei `therapeutic_core.py`→`conversation_core.py` (B5 korrekt) |
| **B12** `PsychologyProfile` | Klasse heißt **`PsychologicalProfile`** (20 Refs, 4 Dateien) | `PsychologicalProfile` → `WellbeingProfile` |
| **B6/B14** `gui.tabs.psychology` | Tab liegt in **`ui_tabs/`**, nicht `gui/tabs/` | `ui_tabs/psychology_tab.py`→`ui_tabs/wellbeing_tab.py`; `render_psychology_tab`→`render_wellbeing_tab` |
| **B15** `psychological.*` | i18n hat **drei** relevante Top-Level-Keys | `psychological.*` (10 Subkeys), **`psych_ui.*`** (7 Subkeys), `gui.tabs.psychology` |

## 2. Exakte DATEI-Rename-Liste (`git mv`)

| # | Vorher | Nachher |
|---|--------|---------|
| D1 | `psychological_support/` (Paket) | `wellbeing/` |
| D2 | `wellbeing/treatment/` (Submodul) | `wellbeing/care_plans/` |
| D3 | `wellbeing/therapeutic_core.py` | `wellbeing/conversation_core.py` |
| D4 | `wellbeing/therapeutic_prompts.py` | `wellbeing/conversation_prompts.py` |
| D5 | `wellbeing/psychological_db.py` | `wellbeing/wellbeing_db.py` |
| D6 | `ui_tabs/psychology_tab.py` | `ui_tabs/wellbeing_tab.py` |
| D7 | `user_context_builder/providers/therapeutic_goals.py` | `user_context_builder/providers/care_goals.py` |

> **Reihenfolge:** D1 → D2 → D3 → D4 → D5 → D6 → D7. `git mv` erhält die Historie. Nach jedem: `py_compile`.

**Betroffene 29 Dateien:** 15 Top-Level in `psychological_support/` — `__init__.py`, `context_summarizer.py`,
`kg_faiss_manager.py`, `mood_progression_tracker.py`, `privacy_handler.py`, `profile_cache_manager.py`,
`profile_synthesis_evaluator.py`, `profile_synthesizer.py`, `psychological_db.py`, `psychological_interface.py`,
`session_manager.py`, `therapeutic_core.py`, `therapeutic_prompts.py`, `topic_extractor.py` —
und 14 in `treatment/` — `__init__.py`, `case_formulator.py`, `extractor.py`, `focus_planner.py`,
`goal_matcher.py`, `llm_json.py`, `manager.py`, `mbc.py`, `models.py`, `repository.py`, `reviewer.py`,
`risk_classifier.py`, `stage_classifier.py`.

## 3. Exakte KLASSEN-Rename-Liste (konservativ)

Nur **user-facing-relevante + Workdoc-B-Liste** — **nicht** alle ~60 Klassen
(`AllianceTracker`, `TechniqueLibrary`, `RuptureDetector`, `PsychoRAGBootstrapper`, `OutcomeMonitor` bleiben intern).

| # | Vorher | Nachher | Refs | Dateien |
|---|--------|---------|------|---------|
| K1 | `PsychologicalDatabase` | `WellbeingDatabase` | 68 | 21 |
| K2 | `PsychologicalSessionManager` | `WellbeingSessionManager` | 28 | 10 |
| K3 | `TreatmentManager` | `CarePlanManager` | 28 | 10 |
| K4 | `PsychologicalProfile` | `WellbeingProfile` | 20 | 4 |
| K5 | `TherapeuticPipeline` | `WellbeingPipeline` | 19 | 3 |
| K6 | `TreatmentPlan` | `CarePlan` | 16 | 6 |
| K7 | `TreatmentRepository` | `CarePlanRepository` | 10 | 6 |
| K8 | `PsychoKGFAISSManager` | *(beibehalten — geteiltes KG-Konzept)* | 8 | 2 |
| K9 | `TherapeuticPromptManager` | `ConversationPromptManager` | 6 | 2 |
| K10 | `PsychologicalTopicExtractor` | `WellbeingTopicExtractor` | 4 | 2 |

> ⚠️ K1+K2+K3 zusammen = **124 Referenzen / ~25 Dateien** → höchste Bruchgefahr.
> **Reihenfolge:** K6/K7 (klein) → K4 → K10 → K9 → K5 → zuletzt K1/K2/K3 (größte).
> `__init__.py` Lazy-Exports aktualisieren: `PsychologicalSessionManager`, `PsychologicalDatabase`,
> `TherapeuticPromptManager` (+ `__all__` + `__getattr__`-Mapping).

---

## 4. Exakte i18n-KEY-Rename-Liste (DE/EN/BG)

| # | Vorher | Nachher | Art |
|---|--------|---------|-----|
| I1 | `psychological.*` (10: `session_title`, `disclaimer`, `mood`, `stress_level`, `reflection`, `goals`, `progress`, `notes`, `context_note`, `crisis`) | `wellbeing.*` | Key **und** Value |
| I2 | `gui.tabs.psychology` | `gui.tabs.wellbeing` | Key+Value („🧠 Psychologie"→„🌱 Wellbeing & Reflexion") |
| I3 | `psych_ui.*` (7: `insight_types`, `chat`, `welcome`, `end`, `active`, `lifecycle`, `goal_progress`, `session`) | *(offen: `wellbeing_ui.*` oder beibehalten)* | offen |

> **Rechtlich zwingend = Value-Änderung** (user-facing Text, nur JSON-Edit).
> **Key-Rename = Konsistenz** (braucht Update aller `t("...")`-Call-Sites: `psychological.*`≈8, `psych_ui.*`≈7, `gui.tabs.psychology`≈1).

---

## 5. Exakte IMPORT-Update-Liste (28 Dateien, ~40 Stellen)

`agent_chatbot_logic.py:734` · `psychological_session/context/context_builder.py:742` ·
`psychological_session/context/session_context_builder.py:413` ·
`psychological_session/handlers/async_message_handler.py:204` ·
`psychological_session/handlers/message_handler.py:250` ·
`psychological_session/services/service_container.py:92,364,381,586` ·
`psychological_session_interface.py:92,98` · `psychological_support/kg_faiss_manager.py:268,438` ·
`psychological_support/profile_cache_manager.py:557` · `psychological_support/session_manager.py:814,1445` ·
`scripts/psychological_session_loader.py:40-42` · `scripts/run_profile_synthesis_eval.py:35,103,104` ·
`tests/test_context_formatter_and_insight_extractor.py:7,8,15,173` · `tests/test_crisis_prompt_threshold.py:12-15,140` ·
`tests/test_fk_recovery.py:25,26` · `tests/test_normalize_whitespace_equiv.py:23` ·
`tests/test_profile_cache_manager.py:10,11` · `tests/test_profile_synthesis_evaluator.py:4,7` ·
`tests/test_profile_synthesizer_sota.py:10` · `tests/test_psychological_db_dedup.py:6` ·
`tests/test_psychological_db_maybe_decrypt.py:19` · `tests/test_psychological_identity_and_mood.py:12` ·
`tests/test_psychological_insight_corrections.py:5` · `tests/test_psychological_user_deletion.py:4,5` ·
`user_context_builder/providers/therapeutic_goals.py:69` · `utils/psychological_orchestrator_integration.py:31,32` ·
`emotional_analyzer.py:274` (Docstring) · `psychological_support/treatment/reviewer.py:193,199,228` (Log-Prefixe).

> **Muster:** `from psychological_support.X import Y` → `from wellbeing.X import Y`;
> `from psychological_support.treatment.Z import W` → `from wellbeing.care_plans.Z import W`;
> interne `from .treatment.models import` → `from .care_plans.models import`.
> **Mock-Pfade** in Tests (`"psychological_support.treatment.risk_classifier..."`) **müssen** mitwandern.

## 6. `get_psychology_path()` → `get_wellbeing_path()`

- **Definition:** `utils/db_path_resolver.py:111` + `__all__` (Zeile 161) — **Rückgabewert bleibt**
  `get_db_path("psychological_support.db")` (§1.2-Datenkompatibilität!)
- **Aufrufer (6):** `config/user_id_config.py:16,37` · `psychological_session/lifecycle/session_lifecycle_manager.py:46-47` ·
  `psychological_session/services/async_startup_service.py:61` · `psychological_session/services/startup_service.py:54` ·
  `scripts/db_backup.py:48,75,85` (Zeile 75: Label `"psychological_support.db"` bleibt als **Dateiname**!) ·
  `psychological_support/psychological_db.py:163` (→ `wellbeing/wellbeing_db.py`)

---

## 7. UI-Tab

`ui_tabs/psychology_tab.py` → `ui_tabs/wellbeing_tab.py` · Funktion `render_psychology_tab` → `render_wellbeing_tab` ·
Call-Sites: `enhanced_streamlit_bot.py:33` (Import), `:619` (Aufruf) ·
i18n `gui.tabs.psychology` → `gui.tabs.wellbeing` (Key + Value, DE/EN/BG) ·
`_get_or_init_psych_interface` (Aufrufer-Parameter, optional konsistent machen).

---

## 8. Exekutions-Reihenfolge (risikogestaffelt, Phasen)

| Phase | Inhalt | Risiko | Erreicht | Gate |
|-------|--------|--------|----------|------|
| **A** | i18n-**VALUES** (Tab-Label, Disclaimer, Krisen-Text) DE/EN/BG · `LEGAL.md`/`TERMS.md` · README/AGENTS/SECURITY-Disklaimer · Compliance-Tests | **niedrig** | **Rechtlicher Launch-Zielzustand** | pytest (psych+compliance), license, hygiene |
| **B** | Paket-/Datei-Rename D1–D7 (`git mv`) + alle §5-Imports + §6-Funktion | mittel | Konsistente Modulstruktur | **vollständige** pytest-Suite + Smoke |
| **C** | Klassen-Rename K1–K10 (K8 optional) + §3-Referenzen + `__init__.py` | **hoch** | Konsistente API | **vollständige** pytest-Suite + Smoke 9/9 |
| **D** | i18n-KEY-Rename I1–I3 + alle Key-Call-Sites | mittel | Konsistente Keys | i18n-Parity (DE/EN/BG) |
| **E** | Finale Gates: Full-Suite, `check_licenses --strict`, `check_release_hygiene --strict`, PII/Secret-Scan, `.key`/`.db` untracked, Git clean, Workdoc-Finalisierung, Commit, Tag `v1.0.0` | — | **Launch** | alle Gates grün |

> **Empfohlene Reihenfolge:** A → (Review) → B → C → D → E. **Jede Phase = eigener Commit + Test-Gate.**

---

## 9. Rollback-Verfahren

```powershell
git reset --hard pre-scope-b-rename          # Rollback auf Checkpunkt (vor Scope-B)
# oder:
git checkout checkpoint/pre-scope-b-rename   # (als neuen Branch)
git tag -d pre-scope-b-rename                # optional: Tag entfernen
git branch -D checkpoint/pre-scope-b-rename  # optional: Branch entfernen
# DBs sind UNBERÜHRT (kein DB-Rename) -> Datenkompatibilität bleibt vollständig erhalten.
```

> **Phasen-Rollback:** Jede Phase A–E ist ein eigener Commit. `git revert <phase-sha>` rollt genau eine Phase zurück.

---

## 10. Verifikations-Gates (pro Phase)

1. `python -m py_compile <geänderte .py>` (nach jedem Schreibvorgang — Schicht 2, AGENTS.md)
2. `git grep -nE 'psychological_support\.|treatment\.(models|repository|manager)'` → **0 Treffer** (nach Phase B)
3. `git grep -nE 'PsychologicalDatabase|TherapeuticPipeline|TreatmentPlan'` → **0 Treffer** (nach Phase C)
4. `python -c "import json; [json.load(open(f'i18n/locales/{l}.json',encoding='utf-8')) for l in ['de','en','bg']]"`
5. i18n-Parity: `wellbeing.*` in DE/EN/BG vorhanden, **gleiche Key-Menge** (nach Phase D)
6. pytest: psych-Gruppe (77 Tests) + **vollständige** Suite (nach Phase B, C, E)
7. `python scripts/check_licenses.py --strict`
8. `python scripts/check_release_hygiene.py --strict`
9. PII/Secret-Scan; `git ls-files '*.key' '*.db'` → **leer**
10. `git status` → **clean**

---

## 11. Offene Entscheidungen (benötigen Review vor Start)

1. **Scope:** (a) nur Phase A (rechtlich nötig, niedriges Risiko) · (b) A+B (Paket-Konsistenz) · (c) A+B+C+D (komplett)
2. **K8 `PsychoKGFAISSManager`:** renamen oder beibehalten (geteiltes KG-Konzept)? **Empfehlung: beibehalten**
3. **I3 `psych_ui.*`:** → `wellbeing_ui.*` oder beibehalten?
4. **Nicht-user-facing Klassen** (`AllianceTracker`, `TechniqueLibrary`, `RuptureDetector`, `PsychoRAGBootstrapper`, `OutcomeMonitor` …):
   renamen (Branding) oder **beibehalten**? **Empfehlung: beibehalten** — kein rechtlicher Nutzen, Bruchrisiko.
5. **Reihenfolge:** A→B→C→D→E (empfohlen) oder legal-Block (A+D) zuerst, dann Rename (B+C)?

---

## 12. Status

| Meilenstein | Status |
|-------------|--------|
| Schritt 1 (Code de-klinisieren) | ✅ fertig, committet (HEAD `5f4282a4`) |
| Schritt 2 (Verifikation) | ✅ 77 Tests PASS, py_compile OK, keine verbatim klinischen Items |
| Rollback-Checkpunkt (Scope-B) | ✅ Tag `pre-scope-b-rename` + Branch `checkpoint/pre-scope-b-rename` → `5f4282a4` |
| Scope-B-Plan | ✅ diese Workdoc |
| Phasen C1–C5 (Session 2) | ✅ **2026-08-31 ausgeführt** — Content-Replacement + Paket-Renames + Verifikation |
| C6 (`therapeutic_goals`→`care_goals`) / C7 (`psych_ui.*`→`wellbeing_ui.*`) | ✅ **2026-09-01 ausgeführt** (Review-Session; C6 inkl. idempotenter DB-Migration; C7 inkl. `insight_types`-Lokalisierung de/bg) |
| C8 (`psychological_session_interface.py`→`wellbeing_session_interface.py`) | ✅ **2026-09-01 ausgeführt** (`git mv` + Klasse + 2 Consumer + 3 Tests + 19 Docstrings + `mypy.ini`) |

---

## 13. Worklog (Session 2, 2026-08-31)

### Phase: Content-Replacement (C1–C5)
- **Tool:** `scripts/tmp_session2_rename.py` (Dry-Run + Real-Run, byte-preserving UTF-8,
  Word-Boundary-Guards, i18n-JSON-Spezialregeln J1/J2). Nach Ausführung gelöscht.
- **Real-Run:** 99 Dateien geändert, 1 Binary übersprungen.
- **Zusätzlich manuell (Bare-Token-Lücke des Tools, per Python-Scan gefunden):**
  - `tests/test_goal_ui_rendering.py:18` — Pfad-Komponente `"psychological_session"`
  - `tests/test_compliance_disclaimers.py:178,195` — Pfad-Komponenten
  - `tests/test_fk_recovery.py:30` — Logger-Name → `wellbeing_session`
  - `wellbeing_session/workflow/session_graph.py:444` — Graph-Name → `wellbeing_session`
    (in-memory Attribut, keine Persistenz — sicher)
  - `psychological_session_interface.py:112` — Kommentar
  - `tests/test_psychological_crisis_i18n.py:91` — i18n-Subscript `["psychological"]` → `["wellbeing"]`
  - Test-Fakes konsistent umbenannt: `_FakeWellbeingDatabase`, `FakeWellbeingPipeline`

### Phase: Paket-Rename (Phase 8)
- `git mv psychological_session wellbeing_session` — vollständig (git Status `R`).

### Verifikations-Gates (alle GRÜN)
| Gate | Ergebnis |
|------|----------|
| py_compile (geänderte Module) | ✅ OK |
| pytest Vollsuite | ✅ **940 passed, 1 warning** (Baseline exakt erreicht) |
| i18n JSON-Validität + Locale-Parität | ✅ 558/558/558 Keys, de==en==bg |
| i18n-Keys | ✅ `wellbeing.*` top-level, `gui.tabs.wellbeing`; `prompts.psychological`, `psych_ui.*` **erhalten** |
| Stale-Reference-Scan (Bare-Tokens) | ✅ 0 Treffer aktiv (nur Archive/Workdocs) |
| Protected-Identifiers | ✅ `psychological_support.db`, `psychological_sessions` (Tabelle), `therapeutic_goals`, `K8`/`PsychoKGFAISSManager`, `psychological_session_interface.py`, `treatment_plans`-Referenzen intakt — ⚠️ **2026-09-01 teilweise aufgehoben:** `therapeutic_goals` und `psychological_session_interface.py` wurden per C6/C8-Entscheidung renamiert (siehe §14) |
| Lizenz-Check (`check_licenses.py --strict`) | ✅ OK (Frische + Policy) |
| Release-Hygiene (`check_release_hygiene.py`) | ✅ GRUEN (653 Dateien, 0 FAIL, 0 WARN) |

### Rollback-Referenz
- **Checkpoint:** Tag `pre-s2-rename` / Branch `checkpoint/pre-s2-rename` → Commit `0aa07557`
- **Wiederherstellung:** `git checkout pre-s2-rename -- .` bzw. `git reset --hard 0aa07557`
  (DBs waren nie betroffen — Datenkompatibilität bleibt erhalten)

### Bemerkungen
- `scripts/mypy_cluster_now.py:56` hat einen **vorbestehenden** Syntaxfehler
  (nicht durch diesen Rename verursacht; vor `0aa07557` vorhanden) — separat behandeln.
- C6/C7 wurden am **2026-09-01** in einer separaten Review-Session entschieden und ausgeführt (siehe §14).

---

## 14. Finale Scope-Entscheidung & Ausführung (2026-09-01)

> **Review-Ergebnis:** Sichtbarkeit im **öffentlichen AGPL-Repository** (Dateinamen, Klassennamen,
> String-Literal-Keys sind für jede Person im Repository einsehbar) wird als First-Class-Risiko
> behandelt — nicht nur UI-Sichtbarkeit. Konsequenz: C6, C7 und C8 werden **renamiert**;
> die verbleibenden Tier-C-Identifikatoren bleiben bewusst bestehen (dokumentiertes Residual-Risiko, §14.4).

### 14.1 C6 — `therapeutic_goals` → `care_goals` (ausgeführt ✅)

| Aspekt | Vorher | Nachher |
|--------|--------|---------|
| Datei | `user_context_builder/providers/therapeutic_goals.py` | `user_context_builder/providers/care_goals.py` |
| Klassen | `TherapeuticGoalsProvider` / `TherapeuticGoalsData` / `TherapeuticGoal` / `TherapeuticPrompt` | `CareGoalsProvider` / `CareGoalsData` / `CareGoal` / `CarePrompt` |
| Data-Key | `therapeutic_goals` | `care_goals` (Provider-Name, Pydantic-Feld, Token-Estimator) |
| **DB-Spalte** | `psychological_sessions.therapeutic_goals` | `psychological_sessions.care_goals` — **idempotente Migration** in `wellbeing/wellbeing_db.py` (`ALTER TABLE ... RENAME COLUMN`, SQLite ≥ 3.25, Daten erhalten) |
| Test | `tests/test_therapeutic_goals_provider.py` | `tests/test_care_goals_provider.py` (mitgewandert) |

- **Belege:** 18 Python-Dateien (14 Produktion + 4 Tests), 96 Referenzen; nach Ausführung 0 Code-Treffer.
- **Konsistenz:** `care_goals` schließt sich an den bestehenden `wellbeing/care_plans/`-Namensraum an.
- **Rollback:** `git revert` des C6-Commits + DB-Stand aus `~\bot6_backups\db\auto\` (oder manuelles Rückrename der Spalte).

### 14.2 C7 — `psych_ui.*` → `wellbeing_ui.*` (ausgeführt ✅)

- **Umfang:** 3 Locale-Dateien (de/en/bg, je 153 Keys unter dem Top-Level-Key), **141** Call-Sites in 9 Python-Dateien.
- **Zusätzlich (Option, mitfreigegeben):** `insight_types`-Labels in `de`/`bg` lokalisiert (waren Englisch) —
  nur Werte geändert, Keys unverändert; Parität DE/EN/BG bleibt 558/558/558.
- **Pinned-Tests:** `tests/test_compliance_disclaimers.py` + `tests/test_i18n_consistency.py` aktualisiert.
- **Rollback:** `git revert` (kein Daten-Impact; i18n-Keys sind nicht persistiert).

### 14.3 C8 — `psychological_session_interface.py` → `wellbeing_session_interface.py` (ausgeführt ✅)

- **Umfang:** `git mv` (Historie erhalten) + `PsychologicalSessionInterface` → `WellbeingSessionInterface`
  + 2 Consumer (`enhanced_streamlit_bot.py`, `pydantic_migration_adapter.py`) + 3 Test-Dateien
  + 19 historische Docstrings/Comments + `mypy.ini` (files-Liste) + 3 Doku-Dateien.
- **Rollback:** `git revert` (kein Daten-Impact).

### 14.4 Tier-C-Residual-Risiken (bewusst NICHT renamiert)

| Identifikator | Grund der Beibehaltung |
|---------------|------------------------|
| DB-Datei `psychological_support.db` | Geteilt mit KG; 14 Tabellen; Nutzerdaten; Rename = separates Datenprojekt |
| Tabellen `psychological_sessions` (+13 weitere) | Existierende Nutzerdaten; Rename = Datenmigration (Phase E) |
| Spalte `mood_progression` | Datenkompatibilität |
| `PsychoKGFAISSManager`, `PsychologyKG` | Geteiltes KG-Domain-Konzept |
| `AllianceTracker`, `RuptureDetector`, `TechniqueLibrary`, `OutcomeMonitor` | Intern, nicht user-facing; kein rechtlicher Nutzen |
| `psychological_insights`-Tabellen | Datenkompatibilität |

> **Bewertung:** Diese Identifikatoren sind in der **DB-Struktur** sichtbar (Modul-, Klassen- und
> Key-Namen sind nun de-klinisiert). Sie bleiben als bekanntes, dokumentiertes Residual-Risiko für
> eine künftige Daten-Migrationsphase (Phase E).

### 14.5 Verifikation (alle Gates GRÜN, 2026-09-01)

| Gate | Ergebnis |
|------|----------|
| `git grep 'therapeutic'` (Code) | 0 Treffer (nur Workdocs/Archive) |
| `git grep 'psych_ui'` (Code/JSON) | 0 Treffer |
| `git grep 'psychological_session_interface'` (Code/`mypy.ini`) | 0 Treffer |
| pytest Vollsuite | **946 passed** (nach C6, C7 und C8 jeweils) |
| i18n JSON-Validität + Parität | ✅ 558/558/558, de==en==bg |
| DB-Migration C6 | ✅ Spalte `care_goals` vorhanden, Daten erhalten, `quick_check` OK |
| Import-Smoke-Test | ✅ `agent_chatbot_logic` + `wellbeing_session_interface` + `enhanced_streamlit_bot` |
| Pre-Commit Release-Gate (2×) | ✅ license-check OK + pytest 946 + profile-fixture `gate_passed: true` |
| Lizenz-Check `--strict` | ✅ OK (Frische + Policy) |
