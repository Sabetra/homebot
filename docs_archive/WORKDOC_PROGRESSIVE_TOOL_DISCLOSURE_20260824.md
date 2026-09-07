# WORKDOC: Progressive Tool Disclosure (ReAct-Pfad)

> Status: **ABGESCHLOSSEN** (2026-08-24) — archiviert gem. AGENTS.md.
> Doku-Anker: `funktionen.md` §Q, `docs/00_CONTEXT_MASTER.md` (Konsolidierungshistorik).

## Meta
- **Datum:** 2026-08-24
- **Umfang:** ~828 Zeilen Code (react_agent, tool_profiles, tool_retriever, agent_state) + 3 Test-Suites + Doku
- **Risiko:** mittel (Tool-Visibilität des LLMs geändert; Safety-Nets + Tests gegen Regression)

## 1. Original-Auftrag
Kontextkompaktierung hat den wörtlichen Prompt verdrängt; der Auftrag wurde aus dem
Git-Diff + `funktionen.md` §Q + Test-Suites rekonstruiert:
**Progressive Tool Disclosure** für den ReAct-Pfad: Profil-gated Tool-Pools,
deterministische Finance-Intent-Erweiterung, maximal EIN Capability-Gap-Retry,
Hybrid-Retrieval (BM25+Cosine+RRF) — ohne die dedizierte Finance-Pipeline anzufassen
und ohne schweigende Degradation.

## 2. Verstanden & Anforderungen
- LLM soll pro Tab/Modus nur die relevanten Tool-Schemata sehen (Prompt-Slimming,
  weniger Fehlauswahl).
- Finance-Zugriff darf für den Nutzer **nicht verloren gehen** (Recall-first):
  Intent-Erweiterung nur erweiternd, nie reduzierend.
- Write-Tools (8) bleiben IMMER aus dem ReAct-Pool (dedizierte Finance-Pipeline).
- Leere Pools / Encoder-Fehler / fehlende `rank_bm25` → explizit geloggt, nie still.
- Retries: exakt ein Capability-Gap-Retry (State-Flag `capability_gap_retry`),
  strukturell kein zweiter möglich.

## 3. Design-Entscheidungen
| # | Entscheidung | Begründung |
|---|-------------|------------|
| D1 | Partition in `tool_profiles.py` (CORE=12, ANALYTICS=11, READ=26, WRITE=8) als Single Source of Truth | react_agent + Tests + Doku importieren dieselben Namen; keine Drift |
| D2 | Intent-Erkennung: Regex DE+EN (kein LLM-Call) | deterministisch, hermetisch testbar, null Latenz |
| D3 | `_profile_covers_finance()` als No-op-Check | finance_tab redundante Expansion vermeiden |
| D4 | Retry-Kriterium = Gap-Phrasierung in Antwort **UND** Finance-Domain (Antwort ODER Query) | verhindert False-Positives bei generischen "nicht möglich" |
| D5 | RRF-Konstante k=60 (Cormack et al. 2009) | Standardwert, robust |
| D6 | `_apply_tool_retrieval` nur für große Pools; kleine Pools = Identity | kleiner Pool + Ranking = Overhead ohne Gewinn |
| D7 | `ToolProfile` als `@dataclass` (nicht Pydantic) | kein v1/v2-Vergiftungsrisiko (Lessons aus 00 §6.3), reine Datenklasse |
| D8 | Degradations-Hierarchie: BM25+Cosine → BM25 → Cosine → unveränderter Pool | jeder Ausfall explizit logbar |

## 4. Umsetzung (Dateien)
- `agent/tool_profiles.py` (+113): Partition, `finance_core()`-Accessor, Self-Test-Asserts
- `agent/tool_retriever.py` (neu, 225): `HybridToolRetriever`, `rrf_fuse`, `get_tool_retriever` (Singleton, Registry-Keyed)
- `agent/react_agent.py` (+199): `_new_initial_state`, `_tool_schemas_for_state`,
  `_resolve_tool_pool_names`, `_apply_tool_retrieval`, `_maybe_capability_gap_retry`,
  `_looks_like_capability_gap`, `_profile_covers_finance`
- `agent/agent_state.py` (+33): `tab_mode`, `tool_pool`, `capability_gap_retry` dokumentiert
- Tests: `tests/test_tool_profile_gating.py` (neu), `tests/test_tool_retriever.py` (neu),
  `tests/test_web_search_routing_guard.py` (angepasst, Identity-Passthrough explizit)
- Doku: `funktionen.md` §Q (neu)

## 5. Verifikation
- **97/97** hermetisch: Gating (Pools, Finance-Intent, Write-Nie, Retry-Einmaligkeit),
  Retriever (RRF-Mathematik, Degradation, Singleton, Apply-Hooks), Routing-Guard.
- **121/121** Regression: react_agent, finance_query_tools, finance_pipeline, orchestrator.
- **Kallgraph verifiziert (2026-08-24):** `agent_chatbot_logic.handle_message()` →
  `Orchestrator.process_message()` → `ReActAgent.run()` → `_tool_schemas_for_state()` →
  `_resolve_tool_pool_names()` → `get_tool_retriever().rank()` — Produktionspfad aktiv.

## 6. Live-Bot-Status (ehrliche Bestandsaufnahme 2026-08-24)
**Aktiv im laufenden Bot (tab_mode-Default "main_chat"):**
- ✅ main_chat-Profil-Pool (10 Tools) für den ReAct-Lauf
- ✅ Finance-Intent-Erweiterung: Finance-Query im Main-Chat → FINANCE_CORE (12 Read-Tools)
  kommt zusätzlich in den Pool (Tests decken exakt diesen Pfad ab)
- ✅ Capability-Gap-Retry (One-Shot) hinter `run()`
- ✅ Dedizierte Finance-Tab-Pipeline (Finance-Query → FinancePipeline): unverändert,
  Write- + Analytics-Tools dort weiterhin direkt ausführbar

**Nicht aktiv (bekannt, dokumentiert):**
- ⚠️ `settings["tab_mode"]` wird aktuell von KEINER UI-Datei gesetzt
  (Repo-weite Suche 2026-08-24: nur `react_agent.py` liest es, Default "main_chat").
  Das `finance_tab`-Profil (26 Read-Tools) und `psych_tab` greifen daher im Bot noch nicht.
  **Nutzerseitige Lücke: keine** — Finance-Zugriff kommt über die Intent-Erweiterung
  (CORE) + Gap-Retry + dedizierte Pipeline. Spezialisierte Analytics-Tools (11) sind
  im Main-Chat-Lauf bewusst nicht LLM-auswählbar (Progressive Disclosure);
  `finance_sql_query` (CORE) + Finance-Pipeline decken den Funktionsumfang.
- ⚠️ `Orchestrator._current_tab_mode` hat keinen Setter (Planner-Katalog läuft
  mit main_chat) — Vorzustand dieser Task (Filesystem-Connector), kein Regression.

## 7. Risiken & Mitigationen
| Risiko | Mitigation |
|--------|------------|
| Intent-Regex übersieht Finance-Query | Gap-Retry fängt "kein Zugriff"-Antworten ab; `finance_sql_query` als CORE-Escape-Hatch |
| Encoder/BM25-Fehler | 4-stufige Degradations-Hierarchie, explizite Logs, Pool bleibt vollständig |
| Leerer Pool nach Gating | Fallback auf volles Tool-Set, geloggt (`_tool_schemas_for_state`) |
| Doppel-Retry | State-Flag + Parameter-Default, Test `test_second_retry_impossible` |
| Write-Leak in ReAct-Pool | Partition + Test `test_write_tools_never_in_pool` (parametrisiert über 3 Modi) |

## 8. Open Points / Next Steps
- [ ] (optional) UI-Verdrahtung: `settings["tab_mode"]` in `agent_chatbot_logic.py` /
      `enhanced_streamlit_bot.py` je aktivem Tab setzen → finance_tab/psych_tab-Profile greifen
- [ ] (optional) Commit NUR der Task-Dateien: `agent/react_agent.py`,
      `agent/tool_retriever.py`, `agent/tool_profiles.py`, `agent/agent_state.py`,
      `tests/test_tool_profile_gating.py`, `tests/test_tool_retriever.py`,
      `tests/test_web_search_routing_guard.py`, `funktionen.md`,
      `docs/00_CONTEXT_MASTER.md`, dieses Workdoc
- [ ] (optional) Einmaliger Live-E2E-Check Main-Chat + Finance-Tab zur Runtime-Bestätigung

## 9. Abschluss
Alle 4 Komponenten implementiert, getestet (97+121 PASS), Produktionskallgraph
verifiziert, Doku konsistent. Bekannte Grenzen (UI-Tab-Verdrahtung, Analytics-Tools
im Main-Chat) sind bewusst dokumentiert, keine stillen Ausfälle.
