# Workdoc: Session 2 Rename — Verifizierte Import-/Referenz-Checkliste (Wellbeing-Repositionierung)

> **Erstellt:** 2026-08-31 · **Status:** `CHECKLIST_READY_FOR_REVIEW` — Ausführung wartet auf Freigabe (§10)
> **Autor:** Cline (Agent) · **Reviewer:** Sabetra
> **Verwandt:**
> - `WORKDOC_SCOPE_B_RENAME_PLAN_20260831.md` (Exekutions-/Rollback-Plan, Phasen A–E)
> - `WORKDOC_PUBLIC_LAUNCH_20260831.md` (Launch-Hygiene) · `WORKDOC_LEGAL_DSGVO_PSYCH_20260831.md` (Legal)
> **Rollback-Checkpunkte:** Tag `pre-scope-b-rename` → `5f4282a4` · Tag `wellbeing-a5-v1.0` (Phase A.5, ✅ committet `13ac32f5`/`d4efa96c`)
> **Basis:** verifizierte Scans `C:\Temp\bot6_s2_scan.txt` (479 Z.) + `C:\Temp\bot6_s2_scan2.txt` (311 Z.) + frische `git grep` (2026-08-31)
> **Status-Update (2026-08-31):** `TRIAGE_DONE · UNKRITISCH_IN_ARBEIT` — Kritisches (§⚡ + §10) wartet auf Review; Unkritisches (U1–U4) wird jetzt umgesetzt

---

## ⚡ TRIAGE — Kritisches (Review) vs. Unkritisches (jetzt umsetzen)

> **Auftrag (Sabetra, 2026-08-31):** Kritisches hier listen + **später reviewen**; Unkritisches
> **jetzt umsetzen**; **regelmäßig dokumentieren** (§14 Worklog), damit bei Fehlern die
> Rollback-/Reparatur-Referenzen da sind.

### 🔴 KRITISCH — Review vor Ausführung (jetzt **nicht** umsetzen)

> Der **gesamte Code-Rename** ist kritisch: breit verdrahtet, teils Scope-Erweiterung,
> teils daten-sensitiv. Fehler hier sind teuer → **warten auf Review**.

| # | Punkt | Umfang | Warum kritisch | Empfehlung |
|---|-------|--------|----------------|------------|
| C1 | Scope-Entscheidung (10.1) | — | bestimmt Gesamtumfang | **A** (`wellbeing/` + `wellbeing_session/`) |
| C2 | D1/D2 Paket-Rename | 28 Dateien, 61 Imports | Kern des Renames, breit verdrahtet | nach Review |
| C3 | K-Classes K1–K7/K9/K10 | ~199 Refs, 21 Dateien | breites Refactoring | Reihenfolge 10.6; **K8 bleibt** |
| C4 | i18n I1+I2 | 23 Keys ×3, ~32 Call-Sites | rechtlich relevant (de-klinisieren) | renamen |
| C5 | D3 `psychological_session/` | 54 Dateien, 85 Imports | größter Block, Scope-Erw. | **eigene Phase** (10.4) |
| C6 | F5 `therapeutic_goals.py` | — | DB-Spalte/Provider/Data-Key (§6.2) | **beibehalten** (10.2) |
| C7 | I3 `psych_ui.*` | 153 Keys ×3, ~100 Call-Sites | kein rechtl. Nutzen, hohes Bruchrisiko | **beibehalten** (10.3) |

### 🟢 UNKRITISCH — jetzt umgesetzt (sicher, reversibel, kein Daten-/Entscheidungs-Risiko)

| # | Aktion | Zweck | Status |
|---|--------|-------|--------|
| U1 | Tag `pre-s2-rename` + Branch `checkpoint/pre-s2-rename` (→ `0aa07557`) | Rollback-Punkt (§11 Phase 0) | ✅ |
| U2 | Workdoc in `docs/README.md` registriert | Auffindbarkeit (Konvention) | ⏳ |
| U3 | Baseline-Test (pytest-Volllauf) als „vorher"-Referenz | Fehler-Vergleich nach Rename | ✅ **940 passed / 1 warning** (vorhanden) |
| U4 | Triage + Worklog hier dokumentiert | Referenz für Review/Reparatur | ✅ (diese Sektion) + §14 |

> **Begründung:** U1–U4 sind **risikofrei**, **reversibel** und schaffen genau die
> **Referenz-Basis**, die für die spätere kritische Ausführung + Fehlerbehebung nötig ist.

---

## 0. Ziel & Abgrenzung

**Ziel:** Eine **einzige, verifizierte, nicht-DB-Checkliste** für das Session-2-Paket-Rename der
**öffentlichen Paketstruktur**:

| Paket/Datum | → |
|-------------|---|
| `psychological_support/` | `wellbeing/` |
| `psychological_support/treatment/` | `wellbeing/care_plans/` |
| `psychological_session/` | `wellbeing_session/` |

**Abgrenzung (kritisch):**
- **KEINE DB-Änderung:** keine DB-Datei-, Tabellen-, Spalten- oder Migrationsänderung (Phase E / Datenkompatibilität).
- **NUR Code-/Paket-/i18n-/UI-Ebene.**
- **Daten-sensible Identifier bleiben** (`therapeutic_goals`, `mood_progression` als DB-Spalte / Provider-Name / Data-Key) — s. §6.
- Phase A.5 (User-/LLM-sichtbare Strings de-klinisieren) ist **bereits fertig** — diese Checkliste ist der **nächste** Schritt (Struktur-Name).

> ⚠️ **Korrekturen gegenüber `WORKDOC_SCOPE_B_RENAME_PLAN`:**
> 1. **i18n-Scope war massiv unterschätzt** — echt sind **22** `psychological.*` + **153** `psych_ui.*` + 1 Tab-Key (nicht „10 + 7 + 1").
> 2. **`psychological_session/` → `wellbeing_session/`** ist Teil der bestätigten Mapping, **nicht** im ursprünglichen Scope-B-Plan → hier neu abgegrenzt (§8).
> 3. **D7 `therapeutic_goals.py`** kollidiert mit dem Data-Sensitivity-Finding (liegt **außerhalb** des Pakets) → zu Entscheidung §10.

---

## 1. Exakte Rename-Mapping (Pakete / Dateien / Funktionen)

### 1.1 Pakete / Verzeichnisse (`git mv`)

| # | Vorher | Nachher | Umfang |
|---|--------|---------|--------|
| D1 | `psychological_support/` | `wellbeing/` | 15 Top-`.py` + `treatment/` |
| D2 | `wellbeing/treatment/` | `wellbeing/care_plans/` | 13 `.py` |
| D3* | `psychological_session/` | `wellbeing_session/` | 54 `.py` · 9 Subpakete |

> \* D3 = **Scope-Erweiterung** (bestätigte Mapping, nicht im Scope-B-Plan). s. §8.

### 1.2 Dateien (`git mv`, nach D1/D2)

| # | Vorher | Nachher |
|---|--------|---------|
| F1 | `wellbeing/therapeutic_core.py` | `wellbeing/conversation_core.py` |
| F2 | `wellbeing/therapeutic_prompts.py` | `wellbeing/conversation_prompts.py` |
| F3 | `wellbeing/psychological_db.py` | `wellbeing/wellbeing_db.py` |
| F4 | `ui_tabs/psychology_tab.py` | `ui_tabs/wellbeing_tab.py` |
| F5 ⚠️ | `user_context_builder/providers/therapeutic_goals.py` | `user_context_builder/providers/care_goals.py` |

> \* **F5 = Entscheidungspunkt** (§10.2): außerhalb des Pakets; Dateiname renambar, aber der **Provider-Name / Data-Key / DB-Spalte `therapeutic_goals` bleiben** (§6).

### 1.3 Funktionen / Symbole

| Vorher | Nachher |
|--------|---------|
| `get_psychology_path()` | `get_wellbeing_path()` — **Rückgabewert unverändert** (`psychological_support.db`) |
| `render_psychology_tab()` | `render_wellbeing_tab()` |

---

## 2. Import-Checkliste (P1–P4) — Datei:Zeile

> **Muster:**
> `from psychological_support.X import Y` → `from wellbeing.X import Y`
> `from psychological_support.treatment.Z import W` → `from wellbeing.care_plans.Z import W`
> `from psychological_session.X import Y` → `from wellbeing_session.X import Y`
> interne `from .treatment.Z import W` → `from .care_plans.Z import W`
> **Mock-Pfade** in Tests (`"psychological_support.treatment.risk_classifier..."`) **müssen** mitwandern.

### P1 `from psychological_support.` — **48 Import-Zeilen**

| Datei | Zeile(n) |
|-------|----------|
| `agent_chatbot_logic.py` | 734 |
| `psychological_session/context/context_builder.py` | 742 |
| `psychological_session/context/session_context_builder.py` | 413 |
| `psychological_session/handlers/async_message_handler.py` | 204 |
| `psychological_session/handlers/message_handler.py` | 250 |
| `psychological_session/services/service_container.py` | 92, 364, 381, 586 |
| `psychological_session_interface.py` | 92, 98 |
| `wellbeing/kg_faiss_manager.py` | 268, 438 |
| `wellbeing/profile_cache_manager.py` | 557 |
| `wellbeing/profile_synthesis_evaluator.py` | 16 |
| `wellbeing/psychological_db.py` | 216 |
| `wellbeing/session_manager.py` | 145, 814 |
| `scripts/psychological_session_loader.py` | 40, 41, 42 |
| `scripts/run_profile_synthesis_eval.py` | 35, 103, 104 |
| `tests/test_context_formatter_and_insight_extractor.py` | 7, 8, 15, 173 |
| `tests/test_crisis_prompt_threshold.py` | 12, 13, 14, 15 |
| `tests/test_fk_recovery.py` | 25 |
| `tests/test_normalize_whitespace_equiv.py` | 23 |
| `tests/test_profile_cache_manager.py` | 10, 11 |
| `tests/test_profile_synthesis_evaluator.py` | 4, 7 |
| `tests/test_profile_synthesizer_sota.py` | 10 |
| `tests/test_psychological_db_dedup.py` | 6 |
| `tests/test_psychological_db_maybe_decrypt.py` | 19 |
| `tests/test_psychological_identity_and_mood.py` | 12 |
| `tests/test_psychological_insight_corrections.py` | 5 |
| `tests/test_psychological_user_deletion.py` | 4, 5 |
| `user_context_builder/providers/therapeutic_goals.py` | 69 |
| `utils/psychological_orchestrator_integration.py` | 31, 32 |

### P2 `from psychological_support.treatment.` — **11 Zeilen** (Teilmenge von P1)

`context_builder.py:742` · `session_context_builder.py:413` · `async_message_handler.py:204` ·
`message_handler.py:250` · `test_context_formatter_and_insight_extractor.py:8,15,173` ·
`test_crisis_prompt_threshold.py:13,14,15` · `user_context_builder/providers/therapeutic_goals.py:69`

### P3 `from psychological_session.` — **85 Import-Zeilen** (nur relevant bei D3)

> Verteilt über ~30 Dateien (Root, `enhanced_streamlit_bot.py`, `scripts/`, `tests/`,
> `user_context_builder/`). Vollständige Liste: `C:\Temp\bot6_s2_scan.txt` Abschnitt P3.
> **Beispiele:** `enhanced_streamlit_bot.py`, `scripts/validate_goal_ui_renderer.py:16,85`,
> `tests/test_context_formatter_and_insight_extractor.py:5,6`, `tests/test_family_entity_boost.py:5`,
> `tests/test_fk_recovery.py:27`, `tests/test_goal_ui_rendering.py:170,181,189`,
> `tests/test_langchain_adapter.py:7`, `tests/test_profile_cache_handler_invalidation.py:8,9`,
> `tests/test_psych_response_provenance.py:1`, `tests/test_psychological_chat_input_handler.py:4,5,6`,
> `tests/test_psychological_context_builder_failfast.py:5,6`, `tests/test_psychological_crisis_i18n.py:6`,
> `tests/test_psychological_identity_and_mood.py:8,9,10,11`, `tests/test_psychological_startup_cleanup.py:5,6,7`,
> `tests/test_token_budget_fail_closed.py:5`, `tests/test_token_budget_trim_equivalence.py:15`,
> `user_context_builder/providers/knowledge_graph.py:97,98`.

### P4 relative `.treatment` (innerhalb `wellbeing/`) — **2 Zeilen**

| Datei | Zeile |
|-------|-------|
| `wellbeing/session_manager.py` | 23 (`from .treatment import TreatmentManager`), 1445 (`from .treatment.models import GoalStatus as _GS`) |

### P5 interne relative Imports in `psychological_session/` — **~30** (nur bei D3)

`from .X import Y` / `from ..X import Y` innerhalb der 9 Subpakete; bleiben bei reiner
Paket-Umbenennung `psychological_session/`→`wellbeing_session/` **unverändert** (keine Submodul-Datei-Renames).

---

## 3. Pfad-Helfer `get_psychology_path()` — **14 Referenzen**

> **Definition:** `utils/db_path_resolver.py:111` + `__all__` (`:161`).
> **Rückgabewert bleibt** `get_db_path("psychological_support.db")` (Datenkompatibilität, §9).

| Datei | Zeile | Art |
|-------|-------|-----|
| `utils/db_path_resolver.py` | 111, 161 | **Definition** + `__all__` |
| `config/user_id_config.py` | 9 (Doku), 16 (Import), 37 (Aufruf) | Consumer |
| `psychological_session/lifecycle/session_lifecycle_manager.py` | 46, 47 | Consumer |
| `psychological_session/services/async_startup_service.py` | 61 | Consumer |
| `psychological_session/services/startup_service.py` | 54 | Consumer |
| `scripts/db_backup.py` | 48, 75, 85 | Consumer (`:75` Label `"psychological_support.db"` bleibt **Dateiname**!) |
| `psychological_support/psychological_db.py` | 163, 164 | Consumer (→ `wellbeing/wellbeing_db.py`) |

---

## 4. UI-Tab / Helper — **16 Referenzen**

| Datei | Zeile | Art |
|-------|-------|-----|
| `ui_tabs/psychology_tab.py` | 9 | **Definition** `render_psychology_tab` (→ F4/§1.3) |
| `enhanced_streamlit_bot.py` | 33 (Import), 619 (Aufruf) | Consumer |
| `enhanced_streamlit_bot.py` | 173 | `_get_or_init_psych_interface` (Aufrufer-Parameter, optional konsistent) |
| `agent_chatbot_logic.py` | 324, 3208, 3210, 3217, 3224, 3231, 3234, 3239, 3249, 3251 | `self.psychological_interface` (Root-Interface, **kein** UI-Tab — nur Namens-Konsistenz) |
| `psychological_support/topic_extractor.py` | 34 | Docstring-Erwähnung |
| `utils/phase3_code_cleanup.py` | 214 | Pfad-Liste (Legacy-Cleanup-Skript) |

> **Tab-Label (Value)** bereits in Phase A.5 de-kliniziert („🧠 Psychologie" → „🌱 Wellbeing & Reflexion").
> Hier nur noch **Key + Datei + Funktion** (§1.3, I2).

---

## 5. Klassen-Checkliste (K1–K10) — verifizierte Counts

> **Reihenfolge (risikogestaffelt):** K6/K7 (klein) → K4 → K10 → K9 → K5 → zuletzt K1/K2/K3 (größte).
> `wellbeing/__init__.py` Lazy-Exports + `__all__` + `__getattr__`-Mapping mit anpassen.
> ⚠️ K1+K2+K3 zusammen = **120 Referenzen / ~25 Dateien** → höchste Bruchgefahr.

| # | Vorher | Nachher | Refs | Dateien | Status |
|---|--------|---------|-----:|--------:|--------|
| K1 | `PsychologicalDatabase` | `WellbeingDatabase` | **65** | 21 | renamen |
| K2 | `PsychologicalSessionManager` | `WellbeingSessionManager` | **27** | 10 | renamen |
| K3 | `TreatmentManager` | `CarePlanManager` | **28** | 10 | renamen |
| K4 | `PsychologicalProfile` | `WellbeingProfile` | **20** | 4 | renamen |
| K5 | `TherapeuticPipeline` | `WellbeingPipeline` | **16** | 3 | renamen |
| K6 | `TreatmentPlan` | `CarePlan` | **16** | 6 | renamen |
| K7 | `TreatmentRepository` | `CarePlanRepository` | **10** | 6 | renamen |
| K8 | `PsychoKGFAISSManager` | — | **7** | 2 | **BEIBEHALTEN** (geteiltes KG-Konzept) |
| K9 | `TherapeuticPromptManager` | `ConversationPromptManager` | **6** | 2 | renamen |
| K10 | `PsychologicalTopicExtractor` | `WellbeingTopicExtractor` | **4** | 2 | renamen |
| | | | **Σ 199** | (max 21) | |

**Klassendefinitionen (Definitionsort):**
- K1 `wellbeing/psychological_db.py:137` · K2 `wellbeing/session_manager.py` · K3 `wellbeing/care_plans/manager.py:88`
- K4 `psychological_session/types.py:95` (+ `wellbeing/profile_cache_manager.py:557`) · K5 `wellbeing/therapeutic_core.py:2101`
- K6 `wellbeing/care_plans/models.py:129` · K7 `wellbeing/care_plans/repository.py:210` · K8 `wellbeing/kg_faiss_manager.py`
- K9 `wellbeing/therapeutic_prompts.py:20` · K10 `wellbeing/topic_extractor.py:13`

> **Nicht-geprüfte interne Klassen** (bleiben, kein rechtlicher Nutzen, Bruchrisiko):
> `AllianceTracker`, `TechniqueLibrary`, `RuptureDetector`, `PsychoRAGBootstrapper`, `OutcomeMonitor`,
> `ContextSummarizer`, `MoodProgressionTracker`, `PrivacyHandler`, `ProfileSynthesizer`, `ProfileCacheManager` …

---

## 6. `therapeutic_*`-Module/Dateien + Data-Sensitivity-Finding

### 6.1 `therapeutic_*` als DATEI/MODUL (renamierbar)

| Datei | Klasse | Scope |
|-------|--------|-------|
| `wellbeing/therapeutic_core.py` → `conversation_core.py` (F1) | `TherapeuticPipeline` (K5) | Paket-Rename |
| `wellbeing/therapeutic_prompts.py` → `conversation_prompts.py` (F2) | `TherapeuticPromptManager` (K9) | Paket-Rename |
| `user_context_builder/providers/therapeutic_goals.py` (F5 ⚠️) | `TherapeuticGoalsProvider` | **außerhalb** des Pakets |

### 6.2 ⚠️ `therapeutic_goals` als DATEN-IDENTIFIER (ursprünglich „NICHT renamierbar" — **2026-09-01 per C6-Entscheidung doch renamiert, §13**)

**Kritisches Finding:** `therapeutic_goals` ist **nicht nur** ein Dateiname, sondern auch:

| Art | Ort | Beispiel |
|-----|-----|----------|
| **DB-Spalte** | `psychological_sessions.therapeutic_goals` | `wellbeing/psychological_db.py:1556,2987,2996,3005,3024,3025,3039,3058,3059` · `wellbeing/session_manager.py:422,1525` |
| **Provider-Name** | `user_context_builder/providers/therapeutic_goals.py:24` | `super().__init__(name="therapeutic_goals", ...)` |
| **Data-Key** | Context-Builder / Token-Estimator | `user_context_builder/builder.py:318` · `user_context_builder/utils/token_estimator.py:56` |
| **Pydantic-Feld** | `user_context_builder/models.py:97,127` | `therapeutic_goals: TherapeuticGoalsData` |
| **Provider-Diskriminierung** | `user_context_builder/builder.py:273,276` | `elif provider.name == "therapeutic_goals":` |

**Regel:** Bei F5 (Datei-Rename) **dürfen** nur der **Pfad/Modulname** und der **Klassennamen**
(`TherapeuticGoalsProvider`) geändert werden. Der **String `"therapeutic_goals"`** (Provider-Name,
Data-Key, Pydantic-Feld, DB-Spalte) **bleibt unverändert** — sonst brechen:
- bestehende DB-Zeilen (Spaltenzugriff),
- Context-Builder-Diskriminierung (`provider.name == "therapeutic_goals"`),
- Pydantic-Serialization,
- Tests (`tests/test_context_formatter_and_insight_extractor.py:41,56,85,94`, `test_psychological_db_dedup.py:58`, `test_psychological_db_maybe_decrypt.py:7`).

> **Empfehlung §10.2:** F5 **beibehalten** (Datei bleibt `therapeutic_goals.py`), um die
> Data-Identifier-Kopplung nicht zu brechen — **es sei denn** die Review entscheidet anders.
> **Entscheidung 2026-09-01 (C6): anders entschieden** — Vollrename inkl. DB-Spalte per idempotenter
> Migration (Details: `WORKDOC_SCOPE_B_RENAME_PLAN_20260831.md` §14.1).

---

## 7. i18n-Key-Checkliste (I1–I3) — verifizierte Counts (DE/EN/BG)

> **Basis:** je Locale **558** Keys. **Parität** DE/EN/BG ist zwingend (gleiche Key-Menge).
> **Rechtlich zwingend = Value** (user-facing) — ist in Phase A.5 **bereits** de-kliniziert.
> **Key-Rename = Konsistenz** — erfordert Update aller `t("...")`/`i18n_t("...")`-Call-Sites.

| # | Key-Prefix | Keys | ×3 Locales | Call-Sites (.py) | Nachher | Status |
|---|-----------|-----:|-----------:|-----------------:|---------|--------|
| I1 | `psychological.*` | **22** | 66 | ~30 | `wellbeing.*` | renamen |
| I2 | `gui.tabs.psychology` | **1** | 3 | 2 | `gui.tabs.wellbeing` | renamen |
| I3 | `psych_ui.*` | **153** | 459 | **141** | `wellbeing_ui.*` | ✅ **renamiert (2026-09-01, C7)** |

### I1 `psychological.*` — 22 Keys (vollständig)

`session_title`, `disclaimer`, `mood`, `stress_level`, `reflection`, `goals`, `progress`, `notes`,
`context_note`, `crisis.header`, `crisis.intro`, `crisis.line1_name`, `crisis.line1_number`,
`crisis.line1_desc`, `crisis.line2_name`, `crisis.line2_number`, `crisis.line3_name`, `crisis.line3_url`,
`crisis.immediate`, `crisis.safety_question`, `crisis.check_intro`, `crisis.closing`

**Call-Sites (~30):**
- `psychological_session/handlers/chat_input_handler.py:74,78,82,86,87,88,91,92,95,96,99,103,116,120`
- `psychological_session/workflow/langgraph_real.py:326,327,328,329,330,331,332,333,334,471`
- `psychological_session_interface.py:778` (`psychological.disclaimer`)
- `tests/test_psychological_crisis_i18n.py:64,70,81,82,83,84`
- `tests/test_compliance_disclaimers.py:7,27,61,76,117` (`psychological.disclaimer`)

### I2 `gui.tabs.psychology` — 1 Key

**Call-Sites (2):** `enhanced_streamlit_bot.py:586` (Tab-Erzeugung), `:617` (`tab_map[...]`).

### I3 `psych_ui.*` — 153 Keys (✅ renamiert 2026-09-01)

Subgruppen: `chat.*`, `session.*`, `lifecycle.*`, `welcome.*`, `active.*`, `goal.*`, `plan.*`,
`privacy.*`, `insight_types.*`, `focus.*`, `evidence.*`, `summary.*`, `metric_*`, `meta_*`, `scope_*` …

**Call-Site-Dateien (Auszug, ~100 Stellen):**
`psychological_session/handlers/chat_input_handler.py` (199,242,265,293,345,369) ·
`psychological_session/lifecycle/session_lifecycle_manager.py` (41,112,117,141,147,149,…) ·
`psychological_session/ui/session_management_renderer.py` (169–469, ~40 Stellen) ·
`psychological_session/ui/welcome_renderer.py` (47,51,68,69) ·
`psychological_session/ui/active_session_renderer.py` · `psychological_session_interface.py:792` ·
`tests/test_compliance_disclaimers.py:82,110,120–126`.

> **Impact-Analyse I3:** 459 JSON-Einträge + ~100 Call-Sites = **größter einzelner Block**.
> **Kein rechtlicher Nutzen** (Keys sind nicht user-facing; Values sind in Phase A.5 bereits sauber).
> **Empfehlung §10.3:** I3 **beibehalten** (Risiko/Effort-Kalkül), **es sei denn** Review will Vollkonsistenz.
> **Entscheidung 2026-09-01 (C7):** Vollkonsistenz gewählt (öffentliches AGPL-Repo = First-Class-Risiko) —
> 459 JSON-Keys + 141 Call-Sites renamiert; zusätzlich `insight_types`-Labels in de/bg lokalisiert.

---

## 8. `psychological_session/` → `wellbeing_session/` (Scope-Erweiterung)

> **Status:** Teil der bestätigten Rename-Mapping (§1.1 D3), **nicht** im ursprünglichen Scope-B-Plan.
> **Empfehlung §10.4:** eigenständige Phase, **nach** `wellbeing/`-Rename, eigenes Review.

### 8.1 Umfang (verifiziert)

| Metrik | Wert |
|--------|------|
| `.py`-Dateien | **54** |
| Subpakete (9) | `adapters/`, `context/`, `handlers/`, `lifecycle/`, `services/`, `ui/`, `utils/`, `visualizations/`, `workflow/` |
| Externe Imports `from psychological_session.` | **85** (P3, §2) |
| Interne relative Imports | **~30** (bleiben unverändert, §2 P5) |
| Klassen mit `psych-*`-Name | `PsychologicalContextBuilder` (`context/context_builder.py`), `PsychologicalProfile` (`types.py:95` = K4) |

### 8.2 Checkliste (bei D3)

- [ ] `git mv psychological_session wellbeing_session` (Historie erhält)
- [ ] Alle **85** `from psychological_session.X import Y` → `from wellbeing_session.X import Y` (P3-Liste §2)
- [x] `psychological_session_interface.py` (Root): ~~Dateiname bleibt~~ → **2026-09-01 (C8): renamiert** zu `wellbeing_session_interface.py` + Klasse `WellbeingSessionInterface` (Imports wie geplant nach `wellbeing_session`/`wellbeing` mitgewandert)
- [ ] `types.py:95` `PsychologicalProfile` → `WellbeingProfile` (K4, gemeinsam mit `wellbeing/`)
- [ ] `context/context_builder.py` `PsychologicalContextBuilder` → **beibehalten** (interne Klasse, nicht in K-Liste) **oder** `WellbeingContextBuilder` (Entscheidung §10.5)
- [ ] Relative Imports: **unverändert** (nur Paket-Root-Name ändert sich)
- [ ] Mock-Pfade in Tests mitwandern

> ⚠️ **Abhängigkeit:** `wellbeing_session/` importiert von `wellbeing/` (P1) und `wellbeing/care_plans/` (P2).
> **Reihenfolge:** erst `wellbeing/` (D1/D2 + F1–F3 + K-Classes + I-Keys) stabilisieren, dann D3.

---

## 9. OUT-OF-SCOPE (Daten / Phase E / Legal)

> **Keiner dieser Punkte wird in Session 2 angefasst.** (Phase E / Datenkompatibilität / Legal)

| Element | Wert | Grund |
|---------|------|-------|
| **DB-Dateiname** | `psychological_support.db` | Geteilt mit KG (`get_kg_path()`); 14 Tabellen; Rename bricht Nutzerdaten |
| **14 DB-Tabellen** | `psychological_sessions`, `session_interactions`, `screening_results`, `alliance_scores`, `case_formulations`, `homework_tasks`, `cumulative_risk`, `outcome_assessments`, `context_summaries`, `psychological_insights`, `psychological_insight_corrections`, `knowledge_graph_entities`, `triples`, `kg_entities` | Existierende Nutzerdaten; Rename = Migration (Phase E) |
| **DB-Spalte `therapeutic_goals`** | ~~`psychological_sessions.therapeutic_goals`~~ → **2026-09-01 (C6): `care_goals`** (idempotente Migration, Daten erhalten) | ~~Datenkompatibilität (§6.2)~~ → entfallen |
| **DB-Spalte `mood_progression`** | `psychological_sessions.mood_progression` | Datenkompatibilität |
| **Provider-Name / Data-Key** | `"therapeutic_goals"` | Context-Builder-Diskriminierung + Pydantic (§6.2) |
| **KG-Struktur** | `PsychologyKG` (RAG-Domain) | Geteiltes Domain-Konzept |
| **Session-Pfade / RAG-FAISS-Cache-Keys** | — | Daten-/Cache-Kompatibilität |
| **Intentionale klinische Disclaimers** | z. B. Compliance-Texte, Krisen-Hotline | Legal — bewusst klinisch, nicht de-klinisieren |
| **`PsychoKGFAISSManager` (K8)** | — | Geteiltes KG-Konzept |

---

## 10. Entscheidungspunkte (benötigen Review vor Start)

| # | Punkt | Option A (empfohlen) | Option B |
|---|-------|---------------------|----------|
| **10.1** | **Scope** | A+B: `wellbeing/`-Rename (Paket+Klassen+i18n-I1/I2) + `wellbeing_session/` (D3) | Nur `wellbeing/` (Scope B ohne D3) |
| **10.2** | **F5 `therapeutic_goals.py`** | **Beibehalten** (Data-Identifier-Kopplung, §6.2) | → `care_goals.py` (nur Pfad/Klasse, String bleibt) |
| **10.3** | **I3 `psych_ui.*` (153 Keys)** | **Beibehalten** (kein rechtl. Nutzen, 459+100 Stellen) | → `wellbeing_ui.*` (Vollkonsistenz) |
| **10.4** | **D3 `psychological_session/`** | **Ja** (bestätigte Mapping) — eigene Phase nach `wellbeing/` | Nein (bleibt `psychological_session/`) |
| **10.5** | **`PsychologicalContextBuilder`** | **Beibehalten** (interne, nicht K-Liste) | → `WellbeingContextBuilder` |
| **10.6** | **Reihenfolge** | `wellbeing/` (D1→D2→F1–F3→K6/K7→K4→K10→K9→K5→K1/K2/K3→I1/I2) → D3 → Gates | legal-Block (I1/I2) zuerst |

> **Default-Annahme (falls keine explizite Entscheidung):** 10.1=A · 10.2=A · 10.3=A · 10.4=A · 10.5=A · 10.6=A.
> **Begründung:** maximaler rechtlicher + Konsistenz-Nutzen bei kontrolliertem Risiko; I3/F5 als „beibehalten"
> minimieren Bruchfläche ohne rechtlichen Verlust.

---

## 11. Exekutions-Reihenfolge & Gates (nach Freigabe)

> **Jede Phase = eigener Commit + Test-Gate.** Details/Rollback: `WORKDOC_SCOPE_B_RENAME_PLAN_20260831.md` §8–9.

| Phase | Inhalt | Gate |
|-------|--------|------|
| **0** | Backup + Tag `pre-s2-rename` + Branch `checkpoint/pre-s2-rename` | `git log` zeigt Checkpunkt |
| **1** | `git mv` D1 `psychological_support/`→`wellbeing/` · D2 `treatment/`→`care_plans/` | `py_compile` (alle 28 Dateien) |
| **2** | `git mv` F1/F2/F3 (Datei-Renames) | `py_compile` |
| **3** | Imports P1/P2/P4 (48+11+2) + Mock-Pfade | `git grep 'psychological_support\.'` → **0** |
| **4** | K-Classes K6→K7→K4→K10→K9→K5→K1→K2→K3 + `__init__.py` | `git grep 'PsychologicalDatabase\|TreatmentManager\|TherapeuticPipeline'` → **0** (K8 ausgenommen) |
| **5** | `get_psychology_path`→`get_wellbeing_path` (14) | `py_compile` + Import-Smoke |
| **6** | UI: F4 + `render_wellbeing_tab` (16) | `py_compile` |
| **7** | i18n I1 (22×3) + I2 (1×3) + Call-Sites (~32) | i18n-Parität DE/EN/BG + JSON-Valid |
| **8** | D3 `psychological_session/`→`wellbeing_session/` (54 Dateien) + P3 (85) | `git grep 'from psychological_session\.'` → **0** |
| **9** | **Finale Gates** (s. u.) | alle grün |
| **10** | Commit + Tag `wellbeing-s2-v1.0` + Workdoc-Finalisierung | `git status` clean |

**Finale Gates (Phase 9):**
1. `python -m py_compile` (alle geänderten `.py`)
2. **vollständige** pytest-Suite: `powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 tests/ -q --no-header -p no:cacheprovider`
3. `python scripts/check_licenses.py --strict`
4. `python scripts/check_release_hygiene.py --strict`
5. i18n: `python -c "import json;[json.load(open(f'i18n/locales/{l}.json',encoding='utf-8')) for l in ('de','en','bg')]"`
6. i18n-Parität: `wellbeing.*` in DE/EN/BG, **gleiche Key-Menge**
7. PII/Secret-Scan; `git ls-files '*.key' '*.db'` → **leer**
8. `git grep -nE 'psychological_support\.' -- '*.py'` → **0** (nur `wellbeing/`)
9. `git status` → **clean**

---

## 12. Verifikations-Kommandos (für dieses Workdoc verwendet)

```powershell
# Imports
git --no-pager grep -nE 'from psychological_support\.' -- '*.py'          # → 48 (P1)
git --no-pager grep -nE 'from psychological_support\.treatment\.' -- '*.py'  # → 11 (P2)
git --no-pager grep -nE 'from psychological_session\.' -- '*.py'          # → 85 (P3)

# Klassen (pro Klasse)
git --no-pager grep -nE '\bPsychologicalDatabase\b' -- '*.py'            # → 65 (K1)
git --no-pager grep -nE '\bPsychologicalSessionManager\b' -- '*.py'      # → 27 (K2)
git --no-pager grep -nE '\bTreatmentManager\b' -- '*.py'                 # → 28 (K3)
git --no-pager grep -nE '\bPsychologicalProfile\b' -- '*.py'             # → 20 (K4)
git --no-pager grep -nE '\bTherapeuticPipeline\b' -- '*.py'              # → 16 (K5)
git --no-pager grep -nE '\bTreatmentPlan\b' -- '*.py'                    # → 16 (K6)
git --no-pager grep -nE '\bTreatmentRepository\b' -- '*.py'              # → 10 (K7)
git --no-pager grep -nE '\bPsychoKGFAISSManager\b' -- '*.py'             # → 7 (K8, beibehalten)
git --no-pager grep -nE '\bTherapeuticPromptManager\b' -- '*.py'         # → 6 (K9)
git --no-pager grep -nE '\bPsychologicalTopicExtractor\b' -- '*.py'      # → 4 (K10)

# Pfad-Helfer + UI
git --no-pager grep -nE 'get_psychology_path' -- '*.py'                  # → 14
git --no-pager grep -nE 'psychology_tab|render_psychology_tab' -- '*.py' # → 16

# i18n-Keys (Python, zuverlässig)
python -c "import json,glob;[print(f, len([k for k in ... if k.startswith('psychological.')])) for f in glob.glob('i18n/locales/*.json')]"
# → je Locale: psychological.*=22 · psych_ui.*=153 · gui.tabs.psychology=1 · total=558

# therapeutic_goals (Data-Identifier)
git --no-pager grep -nE 'therapeutic_goals' -- '*.py' | Select-String 'name=|provider|column|session.get|\.therapeutic_goals'
```

> **Scan-Artefakte (vollständige Datei:Zeile-Listen):** `C:\Temp\bot6_s2_scan.txt` (479 Z.), `C:\Temp\bot6_s2_scan2.txt` (311 Z.).

---

## 13. Status

| Meilenstein | Status |
|-------------|--------|
| Phase A.5 (Strings de-klinisieren) | ✅ fertig, committet (`13ac32f5`/`d4efa96c`), Tag `wellbeing-a5-v1.0` |
| Session-2-Scan (scan1+scan2) | ✅ abgeschlossen (479+311 Z.) |
| Session-2-Checkliste (dieses Workdoc) | ✅ **fertig — bereit für Review** |
| i18n-Scope-Korrektur (22+153+1) | ✅ dokumentiert (§7) |
| `therapeutic_goals` Data-Sensitivity | ✅ dokumentiert (§6.2) |
| D3 `psychological_session/` Scope | ✅ abgegrenzt (§8) |
| Entscheidungspunkte 10.1–10.6 | ✅ **entschieden (2026-09-01)**: 10.2/10.3 → Vollrename (C6/C7); 10.5 + restliche Beibehaltungen = Tier-C |
| Session-2-Ausführung | ✅ **abgeschlossen (2026-09-01)**: C1–C5 (2026-08-31) + C6 + C7 + C8; 946 Tests × 3 GRÜN |

> **Status (2026-09-01):** §10-Entscheidungen getroffen, C1–C8 vollständig ausgeführt und verifiziert.
> Workdoc wird nach dieser Finalisierung in `docs_archive/` verschoben.

---

## 14. Worklog (regelmäßige Dokumentation — Referenzen für Fehlerbehebung/Rollback)

> **Zweck:** Jede Aktion (unkritisch & kritisch) mit Zeitstempel, Referenz und Ergebnis festhalten.
> So sind bei kritischen Fehlern die **Referenzen da, um sie wieder zu beheben** (Sabetra-Auftrag).
> **Regel:** Nach jeder kritischen Phase (C1–C7) hier **sofort** eine Zeile ergänzen (Gate + Ergebnis + Rollback).

| Zeitstempel | Schritt | Aktion | Referenz | Ergebnis | Rollback-Befehl |
|-------------|---------|--------|----------|----------|-----------------|
| 2026-08-31 | **U1** | Tag `pre-s2-rename` + Branch `checkpoint/pre-s2-rename` (→ `0aa07557`) | §⚡ / §11-P0 | ✅ angelegt | `git tag -d pre-s2-rename; git branch -D checkpoint/pre-s2-rename` |
| 2026-08-31 | **U2** | Workdoc in `docs/README.md` registriert (+ `last-verified`→2026-08-31) | README L42 | ✅ fertig | `git checkout pre-s2-rename -- docs/README.md` |
| 2026-08-31 | **U3** | Baseline-Test (pytest-Volllauf) | §11-Gate2 | ✅ **940 passed / 1 warning** / 70s | — (keine Code-Änderung) |
| 2026-08-31 | **U4** | Triage-Sektion (§⚡) + dieser Worklog (§14) | §⚡ / §14 | ✅ fertig | `git checkout pre-s2-rename -- docs/WORKDOC_SESSION2_IMPORT_REFERENCE_CHECKLIST_20260831.md` |
| — | **C1–C5** | *kritischer Code-Rename* (Session 2) | §⚡ / §10 | ✅ **2026-08-31 ausgeführt** (99 Dateien; 940 passed; Tag `wellbeing-s2-v1.0`) | `git reset --hard pre-s2-rename` |
| 2026-09-01 | **C6** | `therapeutic_goals` → `care_goals` (18 Dateien, 96 Referenzen, DB-Migration) | §6.2 / Scope-B §14.1 | ✅ 946 passed; Migration idempotent; 0 Code-Treffer | `git revert` + DB-Backup `~\bot6_backups\db\auto\` |
| 2026-09-01 | **C7** | `psych_ui.*` → `wellbeing_ui.*` (3 Locales, 141 Call-Sites) + `insight_types`-Lokalisierung de/bg | §7 I3 / Scope-B §14.2 | ✅ 946 passed; Parität 558/558/558 | `git revert` |
| 2026-09-01 | **C8** | `psychological_session_interface.py` → `wellbeing_session_interface.py` + Klasse | §8.2 / Scope-B §14.3 | ✅ 946 passed; Import-Smoke OK | `git revert` |

### Baseline (Vor-Rename-Referenz)
- **Kommando:** `powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 tests/ -q --no-header -p no:cacheprovider`
- **Ergebnis:** `940 passed, 1 warning in 70.24s`
- **Warnung (vorhanden, NICHT rename-bezogen):** `torch/cuda/__init__.py:61` — `pynvml`-Deprecation (FutureWarning)
- **Umfeld:** venv `venv_bot_20260802` (Python 3.12.10) · 99 Testdateien · 940 Tests
- **Ableitungsregel:** Nach dem (kritischen) Rename muss das Ergebnis **≥ 940 passed** sein, **keine neuen Failures**; die `pynvml`-Warnung darf weiterhin die **einzige** Warnung bleiben.

### Rollback-Referenzen (kritisch)
| Typ | Wert | Anmerkung |
|-----|------|-----------|
| **Tag (Session 2)** | `pre-s2-rename` → `0aa07557` | sauberer Vor-Zustand (Rename **nicht** gestartet) |
| **Branch (Session 2)** | `checkpoint/pre-s2-rename` → `0aa07557` | identisch zum Tag |
| **Tag (Scope B)** | `pre-scope-b-rename` → `5f4282a4` | älterer Checkpunkt |
| **Tag (Phase A.5)** | `wellbeing-a5-v1.0` | Strings de-kliniziert (✅ `13ac32f5`/`d4efa96c`) |
| **Full-Rollback** | `git reset --hard pre-s2-rename` | oder selektiv: `git checkout pre-s2-rename -- <pfad>` |
| **DB-Backup** | `~\bot6_backups\db\auto\` | täglich (VACUUM INTO), 7 Stände — AGENTS.md §1b |

---

## 15. Abschluss (2026-09-01)

| Punkt | Status |
|-------|--------|
| C6 `therapeutic_goals` → `care_goals` | ✅ ausgeführt, verifiziert (Scope-B §14.1) |
| C7 `psych_ui.*` → `wellbeing_ui.*` (+ `insight_types`-Lokalisierung de/bg) | ✅ ausgeführt, verifiziert (Scope-B §14.2) |
| C8 `psychological_session_interface.py` → `wellbeing_session_interface.py` | ✅ ausgeführt, verifiziert (Scope-B §14.3) |
| Tier-C-Residual-Risiken (DB-Datei, Tabellen, interne Klassen) | ✅ **bewusst beibehalten** — dokumentiert in Scope-B §14.4 (Daten-Migrationsphase E) |
| pytest Vollsuite | ✅ **946 passed** (nach C6, C7, C8 jeweils) |
| i18n-Parität | ✅ 558/558/558, de==en==bg |
| Pre-Commit Release-Gate | ✅ GRÜN (license-check + pytest + profile-fixture) |
| Workdoc-Archivierung | ✅ `docs/` → `docs_archive/` (dieses + Scope-B-Plan) |
