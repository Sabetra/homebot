# Workdoc: Chat-Perf-Telemetrie (Flaschenhals-Diagnose ohne UX-Kosten)

> **Erstellt:** 2026-09-11 00:00
> **Abgeschlossen:** 2026-09-11 19:50
> **Status:** ABGESCHLOSSEN
> **Autor:** Cline
> **Reviewer:** User (M. Artebas)

---

## Original-Auftrag

„ich möchte wissen, um es in der Pipeline einen Flaschenhals gibt, irgendetwas was sich als Zeitfresser entpuppt. Dabei möchte ich aber nicht durch die Messung die Pipeline oder die UX verlangsamen.“

Vorgelagerte Kontext-Frage (beantwortet in dieser Session): Warum sind bereits gemessene
Performance-Werte nicht in Logs oder DBs verfügbar? (Ergebnis: Messwerte fließen nur als
transiente UI-Events; `performance_metrics.db` tot seit 2025-09-05; `performance_stats`
nur In-Memory; `llama_perf_context()` ungenutzt.)

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| `utils/chat_perf_recorder.py`: Run-Level-Record, bounded Queue, asynchroner DB-Writer | Performance-Optimierung (Follow-up auf Basis der Daten) |
| Hook in `agent_chatbot_logic.stream_chat_events` (1 Zeile) | UI-Änderungen, OTel/andere Dependencies |
| Engine-Metriken via `llama_perf_context()` in `scripts/model_loader.py` (alle LLM-Pfade: `_resilient_llm_call` + `generate_response_stream`) | Per-Token-Messung (explizit ausgeschlossen) |
| `scripts/perf_report.py` (p50/p90/p99 + Overhead-Zerlegung) | Live-LLM-Benchmark in dieser Session (würde LM-Studio-Session stören) |
| Tests + Verifikation (py_compile, pytest) | |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | 1 Record/Run in `chat_perf.db` (TTFT, Total, Route, Steps, Engine-Metriken) | `test_observer_completed_run` + `test_writer_persists_one_row_per_run` | ✅ |
| 2 | Queue begrenzt, Writer asynchron (separater Thread), kein I/O im Producer | `test_record_never_blocks_when_queue_full` | ✅ |
| 3 | Kein neuer Code im Token-Loop (TextDelta-Pfad = 2 Zeilen, nur TTFT-Stempel) | Code-Review `_observe` (utils/chat_perf_recorder.py:410-412) | ✅ |
| 4 | Engine-Metriken (Prefill/Generation/Token/`n_reused`) bei allen LLM-Pfaden erfasst | `test_engine_metrics_capture_and_consume` + `test_engine_metrics_thread_local_isolation` | ✅ |
| 5 | `perf_report.py` liefert p50/p90/p99 je Route/Tag + Pipeline-Overhead-Zerlegung | Funktionstest mit synthetischer DB + CLI-Lauf | ✅ |
| 6 | 1 Log-Zeile pro Run (grep-bar) | `_finalize` (chat_perf_recorder.py:485) + Log-Output im Smoke-Test | ✅ |
| 7 | Bestehende Streaming-Tests + neue Tests grün via venv | `run_pytest_venv.ps1` (12 + 17 passed) | ✅ |
| 8 | Kill-Switch `HOMEBOT_CHAT_PERF_DISABLED=1` funktioniert | `test_kill_switch_disables_recorder` + `test_kill_switch_blocks_engine_capture` | ✅ |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Korrektheit | Robustheit | Performance | Risiko | Entscheidung |
|---|--------|-----|--------|-------------|------------|-------------|--------|--------------|
| A | 1 Record/Run, Ring-Buffer, asynchroner Flusher, Engine-Metriken | Zero Hot-Path-Kosten, belastbare Zerlegung LLM vs. Pipeline, kill-switchbar | Neues Modul (~300 Zeilen) | 9 | 8 | 9 | 2 | **Auswahl** |
| B | Synchroner DB-Write pro Step + Log | Einfachster Code | I/O im Request-Pfad = UX-Risiko, viele Zeilen/Run | 6 | 5 | 4 | 5 | abgelehnt |
| C | Volles OTel-Stack | Industrie-Standard | Neue Dependency (Lizenz-Check!), Overhead, Overkill Single-User | 8 | 7 | 6 | 6 | abgelehnt |

> **Auswahl:** A — der Overhead ist strukturell < Rauschen (Hot-Path: 0; pro Run ~µs;
> Flusher: separater Daemon-Thread). B verletzt explizit den Auftrag („UX nicht verlangsamen“).

## Abhängigkeiten & Stakeholder

| # | Abhängigkeit / Stakeholder | Art | Impact | Status |
|---|---------------------------|-----|--------|--------|
| 1 | `utils/db_path_resolver.get_db_path` | Pfad (Single Source of Truth) | `chat_perf.db` unter `.db_root` | erfüllt |
| 2 | `agent/streaming_events.py` (Event-Vertrag) | nur konsumiert, keine Änderung | Event-Felder bleiben unverändert | erfüllt |
| 3 | `scripts/model_loader.py` | 2 Hook-Stellen (`_resilient_llm_call`, `generate_response_stream`) | Backup `~\homebot_backups\chat_perf_20260911\` | erfüllt |
| 4 | User | akzeptiert neue `chat_perf.db` + Log-Zeile pro Run | gewünscht | offen |

## Verifizierte Fakten

| # | Fakt | Beleg (Datei:Zeile / Symbol) |
|---|------|------------------------------|
| 1 | Events fließen nur an einen Sink (UI-Queue) | `agent_chatbot_logic.py:1316` (`sink=event_queue.put`) |
| 2 | `UsageUpdated` trägt keine Token-Zahlen (nur TTFT) | `agent_chatbot_logic.py:1386` |
| 3 | `chat_messages.metadata` enthält keine Timing-Werte | Live-DB-Check `chat_history.db` (2026-09-04-Zeilen) |
| 4 | `performance_metrics.db` tot seit 2025-09-05 (120+61 Zeilen); Writer in `dead_code_archive/` | Live-DB-Check + `dead_code_archive\performance_monitor.py` |
| 5 | `llama_perf_context()` vorhanden: `t_p_eval_ms, t_eval_ms, n_p_eval, n_eval, n_reused` | `venv_bot_20260802\...\llama_cpp.py:5012` |
| 6 | Alle Nicht-Stream-LLM-Calls laufen durch `_resilient_llm_call` | `model_loader.py:1854, 3009, 3177` |
| 7 | DB-Root = `C:\Users\PC\.local\share\homebot_dbs` | `.db_root`-Marker |
| 8 | Token-Zählung pro Chunk in Python wäre Hot-Path-Kosten → Token-Zahlen aus C++ (`n_p_eval`/`n_eval`) | Design-Entscheidung, Beleg: `llama.py` (perf in C++) |

## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | Dominanter Zeitfresser = LLM-Prefill + Generation; Pipeline-Overhead < 500 ms | offen | `perf_report.py`: Step-Dauern + `total_ms − (LLM-only-Zeit)` nach ≥ 5 Runs |
| 2 | RAG-Retrieval ist bei SIMPLE-Route relevant (< 200 ms) | offen | Step-Dauer „Retrieval/Suche“ im Report |
| 3 | KV-Cache-Reuse (`n_reused`) ist im normalen Chat nahe 0 (kein Prompt-Caching-Setup) | offen | `engine_n_reused` im Report |

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmaßnahme | Status |
|---|--------|--------------------|------------|--------------------|--------|
| 1 | Flusher-Thread blockiert (SQLite-Busyness) | niedrig | niedrig | Flusher ist separater Daemon; Producer wird nie blockiert (Event-Set, nicht Lock-Wait) | aktiv |
| 2 | SQLite-Fehler (locked/busy) | niedrig | niedrig | Record wird verworfen, 1× Log-Warnung; App läuft weiter | aktiv |
| 3 | Falsche Engine-Metrik-Zuordnung (parallele Runs) | niedrig | mittel | Thread-Local (Producer-Thread = LLM-Thread) | aktiv |
| 4 | Observer-Fehler bricht Event-Stream | sehr niedrig | hoch | `sink()` fängt alle Exceptions; Base-Sink läuft vorher | aktiv |
| 5 | Legacy-Performance-Dateien verführen Agenten | mittel | niedrig | in Abschlussbericht dokumentiert; Cleanup als Follow-up | dokumentiert |

## Sicherheits- & PII-Implikationen

| # | Aspekt | Implikation | Gegenmaßnahme |
|---|--------|-------------|---------------|
| 1 | Message-Inhalte | Records enthalten NUR IDs, Routen, Zeiten, Token-Zahlen — keine Inhalte | Schema-Review (Step-Labels sind generische UI-Stufen, keine Nutzerdaten) |
| 2 | Session-IDs | hex-UUIDs, keine PII | — |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `utils/chat_perf_recorder.py` (neu, 581 Zeilen) | Recorder + Observer + Sink-Factory + Engine-Brücke + Kill-Switch | 12/12 passed |
| 2 | `agent_chatbot_logic.py` | Import (Zeile 16) + `sink=chat_perf_recorder.make_recording_sink(event_queue.put)` (Zeile 1321) | 17/17 passed (Streaming-Suites) |
| 3 | `scripts/model_loader.py` | Import (Zeile 85) + Engine-Hooks in `_resilient_llm_call` (1635/1637, 1651/1653) + `generate_response_stream` (3273/3298) | 17/17 passed |
| 4 | `scripts/perf_report.py` (neu, 238 Zeilen) | p50/p90/p99 je Route/Tag + Overhead-Zerlegung + `--route/--days/--json/--all` | Funktionstest + CLI-Lauf OK |
| 5 | `tests/test_chat_perf_telemetry.py` (neu) | 12 Tests | 12/12 passed |
| 6 | `utils/chat_perf_recorder.py` | **Reparatur/Verifikation (Session-Ende):** Vorherige Session hatte einen abgebrochenen Write hinterlassen (Editor-Chunk fehlgeschlagen). Heutige Prüfung: Datei vollständig (581 Zeilen, alle Symbole je genau 1× vorhanden: `ChatPerfRecord`, `_RunState`, `ChatPerfRecorder`, `_SENTINEL`, `_observe`, `_finalize`, `begin/end_llm_call`, `consume_engine_metrics`), `py_compile` grün, 12/12 Tests grün — kein Code-Change nötig | 12/12 passed |

## Rollback-Strategie

| Schritt | Aktion | Befehl / Referenz |
|---------|--------|-------------------|
| 1 | Kill-Switch (sofort, ohne Code) | `HOMEBOT_CHAT_PERF_DISABLED=1` als ENV |
| 2 | Code-Zurücknahme | Backups `~\homebot_backups\chat_perf_20260911\*.bak` zurückschreiben; neue Dateien löschen |
| 3 | DB aufräumen | `C:\Users\PC\.local\share\homebot_dbs\chat_perf.db` löschen |

## Offene Risiken

| # | Risiko | Schweregrad | Maßnahme |
|---|--------|-------------|----------|
| 1 | Legacy-Performance-Dateien (`utils/performance_dashboard.py`, `utils/gui_performance_integration.py`) mit stale Imports | niedrig | Follow-up-Cleanup nach Referenzprüfung |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | `run_pytest_venv.ps1 tests/test_chat_perf_telemetry.py -q` | 12 passed in 0.63 s | 2026-09-11 |
| 2 | `run_pytest_venv.ps1 tests/test_chatbot_logic_streaming.py tests/test_streaming_events.py tests/test_model_loader_streaming.py tests/test_agent_chat_streaming.py -q` | 17 passed in 20.41 s | 2026-09-11 |
| 3 | `venv_bot_20260802\Scripts\python.exe -m py_compile agent_chatbot_logic.py scripts\model_loader.py utils\chat_perf_recorder.py` | Exit 0 | 2026-09-11 |
| 4 | `perf_report.py` Funktionstest (synthetische DB, 6 Runs) + CLI-Lauf mit leeren Daten | p50/p90/p99 + Overhead-Zerlegung korrekt; leerer Zustand → Hinweis statt Fehler | 2026-09-11 |

## Finale Nutzung

| # | Nutzung | Detail |
|---|---------|--------|
| 1 | **Sink-Hook (UI-Pfad)** | `agent_chatbot_logic.py:1321`: `sink=chat_perf_recorder.make_recording_sink(event_queue.put)` — Base-Sink läuft zuerst, Telemetrie best-effort danach |
| 2 | **Engine-Hooks (LLM-Pfad)** | `scripts/model_loader.py`: `begin_llm_call`/`end_llm_call` um `fn()` in `_resilient_llm_call` (Zeilen 1635-1637, 1651-1653; fehlgeschlagene Retries werden NICHT akkumuliert) und um Stream-Konsumierung in `generate_response_stream` (3273-3298; Cancel → Partial-Metriken). Alle übrigen `create_completion`-Aufrufe (1866, 2872, 3021, 3189) laufen durch `_resilient_llm_call` |
| 3 | **Report** | `venv_bot_20260802\Scripts\python.exe scripts\perf_report.py [--route R] [--days N] [--json] [--all]` — DB via `utils/db_path_resolver.get_db_path("chat_perf.db")` |
| 4 | **Kill-Switch** | ENV `HOMEBOT_CHAT_PERF_DISABLED=1` (Recorder + Engine-Capture inaktiv, Hot-Pfad = no-op) |
| 5 | **Log** | 1 strukturierte Zeile/Run: `chat_perf run_id=… route=… status=… ttft_ms=… total_ms=… tok_s=… llm_prefill_ms=…` (grep-bar) |

## Bekannte Grenzen

| # | Grenze | Detail |
|---|--------|--------|
| 1 | Background-LLM-Calls (Vision, KG-Extraktion) | nicht gehookt — bewusst: nicht Teil des Chat-Runs; Thread-Local verhindert Kontamination umgekehrt |
| 2 | `llm_calls` zählt nur Calls im Producer-Thread | Multi-Thread-Runs würden nur die Calls des Sink-Threads sehen (aktuell: alle Calls laufen im Streaming-Thread) |
| 3 | Hypothesen 1-3 (Zeitfresser) | erst nach ≥ 5 realen Chat-Runs per `perf_report.py` falsifizierbar |

---

> **Regeln:**
> - Keine ganzen Quelldateien oder Dokumentationen kopieren.
> - Hypothesen klar von bestätigten Befunden trennen.
> - Bei Abschluss: Workdoc löschen oder bei Auditwert nach `docs_archive/` verschieben.
