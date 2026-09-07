# I18n Bulgarian Extension - Complete Gap Analysis

## User Prompt (Original Task)
```
kannst Du bitte prüfen, was alles geändert werden muss, wenn der Bot und die Gui nicht nur auf Deutsch zur Verfügung stehen sollen, sondern auch auf Bulgarisch.
Beachte: Ich nutze einen PC mit Windows, 64 GB RAM und RTX4090. Als LLM für den Bot nutze ich i.d.R. Gemma4 12B.
```

## Executive Summary

**Scope**: Extend the application from German-only to German + Bulgarian (dual-language support).
**Effort**: ~20-25 hours of systematic work across 6 layers.
**Risk**: Medium - string localization is mechanical but pervasive.

---

## Layer 1: GUI Internationalization (enhanced_streamlit_bot.py)

### Hardcoded German Strings Found
| Line | String | Key |
|------|--------|-----|
| 346 | "### 🤖 Enhanced Chatbot" | `gui.sidebar.title` |
| 347 | "AI · RAG · Knowledge Graph · Finance" | `gui.sidebar.caption` |
| 350 | "Modell" | `gui.sidebar.model_heading` |
| 359 | "Modell-Konfiguration" | `gui.sidebar.model_select` |
| 368 | "🚀 Laden" | `gui.sidebar.load_button` |
| 369 | "Lade AI-System..." | `gui.sidebar.loading_spinner` |
| 373 | "⏹️ Entladen" | `gui.sidebar.unload_button` |
| 378 | "Status" | `gui.sidebar.status_heading` |
| 380 | "✅ AI-System geladen" | `gui.sidebar.ai_loaded` |
| 382 | "⚠️ AI-System nicht geladen" | `gui.sidebar.ai_not_loaded` |
| 384 | "💬 Nachrichten" | `gui.sidebar.messages_metric` |
| 385 | "📥 Feedbacks" | `gui.sidebar.feedbacks_metric` |
| 388 | "Schnellzugriff" | `gui.sidebar.quick_access` |
| 389 | "🔄 Chat zurücksetzen" | `gui.sidebar.reset_chat` |
| 414 | "❌ Tab-Health-Contract verletzt:" | `gui.error.health_contract` |
| 419 | ["💬 Chat", "🧠 Psychologie", "📊 Performance", "📈 Feedback", "📚 RAG", ...] | `gui.tabs.*` |
| 452 | "Psychologische Oberfläche nicht verfügbar:" | `gui.error.psych_unavailable` |
| 460 | "Performance-Tab fehlgeschlagen:" | `gui.error.performance_failed` |
| 472 | "Feedback-Tab fehlgeschlagen:" | `gui.error.feedback_failed` |
| 480 | "RAG-Tab fehlgeschlagen:" | `gui.error.rag_failed` |
| 490 | "Quality-Dashboard fehlgeschlagen:" | `gui.error.quality_failed` |
| 492 | "Quality-Dashboard nicht verfügbar." | `gui.info.quality_unavailable` |
| 502 | "Finanztab fehlgeschlagen:" | `gui.error.finance_failed` |
| 513 | "Einstellungs-Tab fehlgeschlagen:" | `gui.error.settings_failed` |
| 190 | "ModelLoader does not support load_model_by_config" | `gui.error.no_load_fn` |
| 194 | "Model could not be loaded:" | `gui.error.model_load_fail` |
| 215 | "AI initialization failed:" | `gui.error.ai_init_fail` |

**Total GUI strings in main file: ~25**

### Tab Name Localization
```python
# Line 419 - Must be parameterized:
tab_names = ["💬 Chat", "🧠 Psychologie", "📊 Performance", "📈 Feedback", "📚 RAG", ...]
# BG: ["💬 Чат", "🧠 Психология", "📊 Перформанс", "📈 Обратна връзка", "📚 RAG", ...]
```

---

## Layer 2: UI Tab Modules (ui_tabs/ - ~200 strings)

### Files Requiring i18n
| File | Est. Strings | Priority |
|------|-------------|----------|
| `chat_tab.py` (399 lines) | ~35 | CRITICAL - main interaction |
| `psychology_tab.py` (48 lines) | ~8 | HIGH |
| `feedback_tab.py` (37 lines) | ~10 | MEDIUM |
| `performance_tab.py` (28 lines) | ~8 | MEDIUM |
| `settings_tab.py` (33 lines) | ~12 | MEDIUM |
| `rag_documents_tab.py` | ~15 | MEDIUM |
| `stubs/` (5 files) | ~5 | LOW |

### Key Strings from chat_tab.py (Sample)
```
"💾 Speichern" / "🗑️ Löschen"                     # button labels
"Antwort empfangen in {time:.1f}s"               # status messages
"📎 Bilder:" / "📄 Dokumente:"                   # file labels
"Neue Datei..." / "Neues Label..."               # placeholders
"Datei erfolgreich hochgeladen!"                 # success messages
"⚠️ Fehler beim Hochladen:"                     # error messages
"PDF-Verarbeitung:" / "Standard-Verarbeitung:"   # processing labels
"Validierung:" / "Ergebnis:"                    # validation labels
"Es wurden keine Bilder generiert."              # info messages
"Antwort wird generiert..."                      # spinner text
"Keine Bilder verfügbar."                        # empty state
"🔍 Antwort wird generiert..."                   # progress
"Chat-Verlauf" / "Gespeicherte Chats"            # section headers
```

**Approach**: Pass `t` translation function to each `render_*_tab()` call.

---

## Layer 3: System Prompts (agent/ - ~48 prompts)

### Critical Prompt Files
| File | Prompts | Notes |
|------|---------|-------|
| `orchestrator.py` | 4 major prompts | ReAct, context builder, image analyzer, diagram generator |
| `sota_pipeline.py` | 4 prompts | Classification, rephraser, answerer, validator |
| `config_manager.py` | 2 prompts | Schema builder, parameter builder |
| `unified_rag_store.py` | 2 prompts | Classifier, reclassifier |
| `agent_lifecycle.py` | 1 prompt | Health reporter |
| `change_detector.py` | 1 prompt | Change detector |
| `diagram_prompt_builder.py` | ~8 templates | Diagram-specific |
| `image_prompt_builder.py` | ~4 templates | Image-specific |
| `llm_utils/prompt_router.py` | ~8 routed prompts | Routing prompts |
| `llm_utils/analyzers.py` | ~4 prompts | Analysis prompts |
| `llm_utils/response_analyzer.py` | ~2 prompts | Response analysis |
| `llm_utils/followup_question_extractor.py` | 1 prompt | Follow-up extraction |

### Prompt i18n Strategy
System prompts MUST be language-aware. Two approaches:
1. **Prompt templates with language injection**: Append user language to prompt context
2. **Full prompt translation**: Translate entire prompt to target language

**Recommendation**: Approach 1 (append language instruction) is more practical for LLM prompts, as LLMs handle multilingual instructions well. The critical part is the *instruction* to respond in the user's language, not translating the entire system prompt.

Example pattern:
```python
system_prompt = base_prompt + f"\n\nWICHTIG: Antworte auf die Sprache des Nutzers (erkannte Sprache: {user_language})."
```

---

## Layer 4: Psychological Session (psychological_session/ - ~35 prompts)

### Files Requiring i18n
| File | Type | Count |
|------|------|-------|
| `core/prompts.py` | German-only prompts | ~15 system prompts (ALL GERMAN) |
| `core/analyzers.py` | German-only prompts | ~8 analysis prompts |
| `core/response_generator.py` | German-only prompts | ~4 generation prompts |
| `lifecycle/session_lifecycle_manager.py` | German UI strings | ~6 status messages |
| `services/startup_service.py` | German UI strings | ~4 status messages |
| `interface.py` | German UI strings | ~4 interface strings |
| `schemas.py` | German error messages | ~4 validation messages |

### Critical: prompts.py is ENTIRELY German
Every single system prompt in `psychological_session/core/prompts.py` is hardcoded in German. This includes:
- `build_intake_assessment_prompt()`
- `build_risk_prompt()`, `build_stability_prompt()`, `build_progress_prompt()`
- `build_session_prompt()`, `build_empathy_prompt()`, `build_crises_prompt()`
- `build_disclaimer_prompt()`, `build_history_prompt()`
- `build_exit_prompt()`, `build_memory_prompt()`, `build_contradiction_prompt()`
- `build_summary_prompt()`, `build_quality_prompt()`, `build_escalation_prompt()`

**This is the single largest concentration of hardcoded German in the entire codebase.**

---

## Layer 5: Finance Module (finance/ - ~16 prompts)

### Files Requiring i18n
| File | Type | Count |
|------|------|-------|
| `query_planner.py` | German system prompt | 1 |
| `grammar_compiler.py` | German system prompt | 1 |
| `query_reflector.py` | German system prompt | 1 |
| `tab.py` | German UI strings | ~12 |

### Finance Prompt Pattern
All 3 finance prompts start with German instructions:
```python
# query_planner.py L146:
"Du bist ein deterministischer Finance-Query-Planer.\n"
"Aufgabe: Erzeuge genau einen Plan fuer die beste erste Tool-Aktion.\n"

# grammar_compiler.py L70:
"Du bist ein deterministischer SQL-Grammar-Compiler.\n"
"Aufgabe: Übersetze einen strukturierten Query-Plan in gueltigen, sicheren SQL-Code.\n"

# query_reflector.py L60:
"Du bist ein Finance-Reflexions-Gate fuer einen iterativen Tool-Agenten.\n"
"Aufgabe: Entscheide, ob die bisherigen Ergebnisse bereits fuer eine korrekte Antwort reichen, "
```

---

## Layer 6: Language Detection (llm_utils/language_detector.py)

### Current State: ✅ READY
The `LanguageDetector` class already supports Bulgarian (`"bg"`) and German (`"de"`). No changes needed.

### User Input Language Auto-Detection
The `detect()` method returns a `LanguageDetectionResult` with:
- `language`: detected language code (e.g., "bg", "de")
- `confidence`: float confidence score
- `is_safe`: whether the content is safe to process

This provides the foundation for automatic language switching based on user input.

---

## Implementation Phases

### Phase 1: GUI Sidebar & Main Layout ✅ (COMPLETED)
- [x] i18n_manager.py with LocaleManager
- [x] i18n/locales/de.json (~300 keys)
- [x] i18n/locales/bg.json (~300 keys, fully translated)
- [x] i18n/locales/en.json (~300 keys)
- [x] Language selector in sidebar

### Phase 2: GUI Main File i18n (estimated 2 hours)
**File**: `enhanced_streamlit_bot.py`
- [ ] Import i18n_manager
- [ ] Replace ~25 hardcoded strings with `t()` calls
- [ ] Parameterize tab names list
- [ ] Add language to session state initialization

### Phase 3: UI Tab Modules i18n (estimated 4 hours)
**Files**: `ui_tabs/*.py`
- [ ] Add `t: Callable` parameter to each `render_*_tab()` signature
- [ ] Replace ~200 hardcoded strings
- [ ] Update calls in `enhanced_streamlit_bot.py` to pass `t`
- [ ] Translate all new keys to bg.json and en.json

### Phase 4: Agent Prompts i18n (estimated 5 hours)
**Files**: `agent/*.py`, `llm_utils/*.py`
- [ ] Inject language context into system prompts
- [ ] Update orchestrator.py prompts
- [ ] Update sota_pipeline.py prompts
- [ ] Update all llm_utils prompts
- [ ] Test with Bulgarian input

### Phase 5: Psychological Session i18n (estimated 5 hours)
**Files**: `psychological_session/**/*.py`
- [ ] Translate prompts.py prompts (largest single change)
- [ ] Translate analyzers.py, response_generator.py
- [ ] Translate lifecycle & service UI strings
- [ ] Update schemas.py error messages

### Phase 6: Finance Module i18n (estimated 2 hours)
**Files**: `finance/*.py`
- [ ] Translate 3 system prompts
- [ ] Translate UI strings in tab.py

### Phase 7: Testing & Validation (estimated 2 hours)
- [ ] Test language switching in UI
- [ ] Test Bulgarian input -> Bulgarian response flow
- [ ] Test German input -> German response flow
- [ ] Test auto-detection accuracy
- [ ] Verify all tabs render correctly in both languages

---

## Bulgarian Translation Reference

### Core UI Terms (DE -> BG)
| German | Bulgarian |
|--------|-----------|
| Chat | Чат |
| Psychologie | Психология |
| Performance | Перформанс |
| Feedback | Обратна връзка |
| Einstellungen | Настройки |
| Finanzen | Финанси |
| Laden | Зареждане |
| Entladen | Изхвърляне |
| Status | Статус |
| Modell | Модел |
| Nachrichten | Съобщения |
| Chat zurücksetzen | Нулиране на чата |
| Antwort wird generiert... | Генериране на отговор... |
| Speichern | Запазване |
| Löschen | Изтриване |
| Datei hochladen | Качване на файл |
| Dokument | Документ |
| Bild | Изображение |
| Fehler | Грешка |
| Erfolg | Успех |
| Warnung | Предупреждение |

### LLM Prompt Language Strategy
For system prompts, the recommended approach is to:
1. Keep the core prompt structure in German (or English)
2. Append a language directive: `"Antworte auf {user_language}."` / `"Отговори на {user_language}."`
3. The LLM (Gemma4 12B) is capable of understanding German prompts and responding in Bulgarian

This avoids the need to translate ~100 system prompts fully, while still ensuring the user receives responses in their preferred language.

---

## Risk Assessment

| Risk | Level | Mitigation |
|------|-------|-----------|
| Missing translation keys | Medium | Fallback chain: bg -> de -> en |
| Prompt quality degradation | Low | Language directive approach |
| UI string overflow | Low | Emojis remain language-neutral |
| Session state corruption | Low | Language stored separately |
| Performance impact | Negligible | Dict lookup is O(1) |

---

## File Change Summary

| Category | Files to Modify | New Keys Needed |
|----------|----------------|-----------------|
| Main GUI | 1 | ~25 |
| UI Tabs | 7 | ~200 |
| Agent Prompts | 12+ | ~48 (language injection) |
| Psychology | 7+ | ~35 (language injection) |
| Finance | 4 | ~16 |
| **Total** | **~31 files** | **~324 new keys per locale** |

---

## SOTA Considerations

For i18n in LLM applications, the current SOTA approach is:
1. **Automatic language detection** from user input (already implemented)
2. **Language-aware prompt routing** - append language to system prompt
3. **UI localization** via JSON translation files (implemented)
4. **Fallback chains** for missing translations (implemented)
5. **Runtime language switching** without restart (to be implemented)

The current infrastructure (i18n_manager + LanguageDetector) already follows SOTA patterns. The remaining work is mechanical: replacing hardcoded strings with `t()` calls and adding language context to prompts.