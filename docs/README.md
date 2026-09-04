<!-- last-verified: 2026-08-31 -->

# Dokumentation – Index (Konsolidiert)

Zweck: Zentrale Übersicht aller aktuellen Projekt-Dokumentationen.
Stand: 2026-08-20 (Doku-Audit)

---

## Neue Dokumentationsstruktur

| Datei | Beschreibung |
|-------|-------------|
| [../AGENTS.md](../AGENTS.md) | **Agent-Einstieg (Root)** – agents.md-Standard: Kommandos, Konventionen, GPU-Parameter |
| [../LICENSE](../LICENSE) | Projekt-Lizenz: AGPL-3.0 (GNU Affero GPL v3), Copyright Michaël Artebas |
| [../LICENSES.md](../LICENSES.md) | Third-Party-Lizenz-Inventar (generiert: `scripts/generate_licenses.py`) |
| [../SUPPORT.md](../SUPPORT.md) | Support (local-only: Doku, Diagnose-Befehle, Logs) |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Mitwirkungsregeln: Setup, Gates, Dateiintegrität, Doku-Pflicht |
| [../CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | Code of Conduct (orientiert am Contributor Covenant 1.4) |
| [../SECURITY.md](../SECURITY.md) | Sicherheitspolitik: local-only-Modell, Schwachstellen melden, Scans |
| [00_CONTEXT_MASTER.md](00_CONTEXT_MASTER.md) | **Haupteinstieg** – Schnellreferenz für LLM-Kontext |
| [01_ARCHITECTURE_DEEP_DIVE.md](01_ARCHITECTURE_DEEP_DIVE.md) | Architektur-Details und Module |
| [02_SOTA_ROADMAP.md](02_SOTA_ROADMAP.md) | SOTA-Strategie und Optimierungsroadmap |
| [03_FINANCE_MODULE.md](03_FINANCE_MODULE.md) | Finanzmodul: Query Planning, RAG, Reflection |
| [04_I18N_GUIDE.md](04_I18N_GUIDE.md) | Internationalisierung (I18n) |
| [05_DEVELOPER_GUIDE.md](05_DEVELOPER_GUIDE.md) | Entwickler-Setup, Testing, Debugging |
| [06_CONTEXT_ENGINEERING_SOTA.md](06_CONTEXT_ENGINEERING_SOTA.md) | Context Engineering SOTA — LLM-Kontext-Optimierung |
| [08_PSYCH_MODULE_OPTIMIZATION.md](08_PSYCH_MODULE_OPTIMIZATION.md) | Psych-Modul: Safety, Identity, Datenlebenszyklus, Persistenz, i18n und LangGraph |
| [14_KG_COMMUNITY_DETECTION_IMPLEMENTATION.md](14_KG_COMMUNITY_DETECTION_IMPLEMENTATION.md) | KG Community Detection: eigenstaendige Implementierung und Tests; produktive Integration noch offen |
| [15_STREAMING_ARCHITECTURE.md](15_STREAMING_ARCHITECTURE.md) | Normaler Chat: typisierte Events, Token-Streaming, Cancellation und atomare Persistenz |
| [16_DEPENDENCY_SCANNER.md](16_DEPENDENCY_SCANNER.md) | Dependency Vulnerability Scanner: lokaler, privacy-preserving Security-Scan für Python-Dependencies |
| [17_FILESYSTEM_CONNECTOR.md](17_FILESYSTEM_CONNECTOR.md) | SOTA Filesystem Connector (2026): Path-Sandbox, Declarative Tool Profiles, Security-Layer |
| [18_LEGAL_WEB_PERSIST.md](18_LEGAL_WEB_PERSIST.md) | Legal/Ethical Compliance für web-sourced RAG-Persistierung: robots.txt/Header/Meta-Gates, Retention (30 d), Pruning, CPython-Robots-Quirks |
| [19_LICENSES_AND_COMPLIANCE.md](19_LICENSES_AND_COMPLIANCE.md) | Lizenzen & Compliance: AGPL-3.0 (Copyright Michaël Artebas), `LICENSES.md`-Generator/Checker, Klassifizierung, Modell-Gewichte, Workflow |
| [20_TOKEN_SCALING.md](20_TOKEN_SCALING.md) | Token/Context-Skalierung: hardware-bewusster Auto-Vorschlag (Sweet Spot), Präzedenz UI > ENV > Auto, KV-Quantisierung, Persistenz, Fallback-Regeln |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | Legacy-Architektur-Referenz (root); aktuell kanonisch: 01_ARCHITECTURE_DEEP_DIVE.md |
| [../DEVELOPER_QUICK_START.md](../DEVELOPER_QUICK_START.md) | Entwickler-Quickstart (root): Setup, Start, Tests |
| [../VISUALIZATION_GUIDE.md](../VISUALIZATION_GUIDE.md) | Visualisierung (root): Mermaid, Graphviz, Charts — 100% lokal |
| [../ARCHIVE_INDEX.md](../ARCHIVE_INDEX.md) | Index aller archivierten Dokumentationen (root) |
| [../funktionen.md](../funktionen.md) | Kompendium großer/komplexer Funktionen (root); vor Orchestrator-/Pipeline-Änderungen lesen |
| [RTX4090_RYZEN9_GUIDE.md](RTX4090_RYZEN9_GUIDE.md) | Hardware-Tuning – verifiziertes LLM-Profil (n_batch=3072) |
| [WORKDOC_CODEBASE_AUDIT_20260828.md](09_archived/WORKDOC_CODEBASE_AUDIT_20260828.md) | **Archiviertes Workdoc (2026-08-28)** – Codebase-Audit: Redundanzen, Dead Code, Repo-Hygiene, Performance; Phasenplan + Rollback |
| [../docs_archive/WORKDOC_SESSION2_IMPORT_REFERENCE_CHECKLIST_20260831.md](../docs_archive/WORKDOC_SESSION2_IMPORT_REFERENCE_CHECKLIST_20260831.md) | **Archiviertes Workdoc (2026-08-31, finalisiert 2026-09-01)** – Session-2-Rename: verifizierte Import-/Referenz-Checkliste (Wellbeing), Triage kritisch/unkritisch, Rollback-Referenzen |
| [../docs_archive/WORKDOC_SCOPE_B_RENAME_PLAN_20260831.md](../docs_archive/WORKDOC_SCOPE_B_RENAME_PLAN_20260831.md) | **Archiviertes Workdoc (2026-08-31, finalisiert 2026-09-01)** – Scope-B-Rename: Exekutions- & Rollback-Plan (Wellbeing-Repositionierung) inkl. C6/C7/C8-Entscheidungen (2026-09-01) + Tier-C-Residual-Risiken |
| [../docs_archive/WORKDOC_LEGAL_DSGVO_PSYCH_20260831.md](../docs_archive/WORKDOC_LEGAL_DSGVO_PSYCH_20260831.md) | **Archiviertes Workdoc (2026-08-31, finalisiert 2026-09-01)** – Legal & Compliance CH/EU/DE + Mental-Health-Positionierung (Public Launch): Screening-Optionen, Scope-B-Rename-Plan (B-Phase), Content-Neupositionierung |

## Archiv

Alte und obsolete Dokumentationen wurden in [09_archived/](09_archived/) verschoben.

---

## Pflege

Bei neuen Docs:
1. In dieser Datei eintragen
2. Eine freie Nummer verwenden; bestehende Nummern nicht umdeuten
3. In 00_CONTEXT_MASTER.md referenzieren

## Templates

| Template | Verwendung |
|----------|-----------|
| [templates/WORKDOC_TEMPLATE.md](templates/WORKDOC_TEMPLATE.md) | Temporäres Workdoc für komplexe Tasks (SOTA: DoD, Alternativen, Risiko, PII, Rollback) |
| [PROMPT_STANDARD.md](PROMPT_STANDARD.md) | Standard-Prompt für Agenten-Aufgaben (Vorgehen 1–15, sync mit WORKDOC_TEMPLATE) |

> **Hinweis:** Workdocs werden nach Abschluss gelöscht oder nach `docs_archive/` verschoben. Sie gehören nicht in den aktiven `docs/`-Ordner.

---

## Release Notes

### 2026-08-20 – Doku-Audit (Aktualität)

- Vollständiger Audit aller aktiven Doks (23 Doks; Web-Recherche + Code-Vergleich).
  Details: `docs_archive/WORKDOC_DOKU_AKTUALITAET_20260820.md` (Workdoc, nach Abschluss
  archiviert gemäß AGENTS.md-Konvention).
- Index um 5 aktive Root-Doks ergänzt (ARCHITECTURE, DEVELOPER_QUICK_START,
  VISUALIZATION_GUIDE, ARCHIVE_INDEX, funktionen).
- Abgegangene Referenzen bereinigt: 07_KG_SOTA_ANALYSIS (AGENTS.md, 14_KG, 00_CONTEXT_MASTER),
  `launch_enhanced_chatbot.py` (root README, QUICK_START), `internet_image_integrator.py`
  (VISUALIZATION_GUIDE), I18N-Quelldokus (04_I18N), CJK-Artefakte (02, 08, funktionen).
- `last-verified`-Header der geänderten Doks aktualisiert.

### 2026-08-10 – Datenbank-Backup aktiviert

- `scripts/db_backup.py`: taegliches Backup aller produktiven DBs nach
  `~\homebot_backups\db\auto\<datum>\`, 7 Generationen, Rotation automatisch.
- **`VACUUM INTO` statt Dateikopie** — transaktional konsistent auch bei laufender
  App; Quelle wird read-only geoeffnet (`mode=ro`) und kann nie veraendert werden.
- Quellpfade kommen ausschliesslich aus `utils/db_path_resolver`, folgen also
  automatisch dem DB-Root. Der Psycho-Schluessel (`.db.key`) wird mitgesichert.
- Atomar: Schreiben nach `<datum>.part`, Umbenennung erst nach fehlerfreiem Lauf.
  Fehlende Quelle oder `quick_check != ok` gilt als Fehler (kein stilles Ueberspringen).
- Angebunden an `scripts/autosave_watcher.ps1` (kein Aufgabenplaner-Task — der ist
  auf dieser Maschine funktionsuntuechtig). Selbst-Gate: zweiter Lauf am selben Tag
  beendet sich in 0,2 s. Backup-Fehler blockieren den Git-Snapshot nicht und umgekehrt.
- **Verifikation:** 484 MB in 3,7 s; alle `quick_check: ok`; Backup und Quelle
  zeilenweise inhaltsgleich (rag_store 307.135 Zeilen ueber 19 Tabellen,
  psych 13.746/35, chat 474/6, finance 23.236/20); Rotation mit 8 Staenden getestet.
- **Grenze:** gleiche Platte — schuetzt gegen Softwarefehler und Loeschung,
  nicht gegen Plattenausfall.

### 2026-08-10 – DB-Wurzel-Split behoben (Root-Cause + Datenumzug)

- **Befund:** Seit Einfuehrung von `.db_root` (2026-07-29) existierten produktive DBs
  doppelt: Resolver-Nutzer (Psycho, Chat, Finance) schrieben nach `bot6_dbs`,
  Resolver-Umgeher (RAG-Store via Workspace-Normalisierung in `agent/tools.py`,
  Web-Policy via CWD-Pfad, StrixKAT via CWD-Literale) schrieben weiter ins Repo.
  Inhalte divergierten (Hash-verifiziert).
- **Code-Fixes:** `agent/tools.py` (get_global_rag_store + ToolManager),
  `agent/unified_rag_store.py` (get_shared/get_existing_shared/Fabriken),
  `agent/web_policy.py`, `agent_toolkit.py`, `agent/strixkat_eval.py` — alle
  Default-Pfade laufen jetzt ueber `utils/db_path_resolver`.
- **Datenumzug** (App gestoppt, WAL-checkpointed, SHA256-verifiziert): lebende
  `rag_store.db` (471 MB, 50k Chunks), `web_policy.db` und `agent/rag_store.db`
  nach `bot6_dbs`; verwaiste Altstaende (inkl. Psycho-Altkopie **mit eigenem Key**)
  nach `~\bot6_backups\db\` archiviert — nichts geloescht.
- **Repo-Hygiene:** veralteter `psychological_support_kg_cache/` (18 MB Binaerdaten)
  aus Git entfernt und archiviert; `.gitignore` ergaenzt. Der lebende Cache folgt
  automatisch dem Psycho-DB-Pfad in `bot6_dbs`.
- **Doku/Konventionen:** `01_ARCHITECTURE_DEEP_DIVE.md` §8a.4 (DB-Wurzel-Vertrag),
  AGENTS.md-Konvention (Resolver-Pflicht). Verifikation: 624 Tests PASS,
  integrity_check ok, Smoke-Tests auf umgezogenen Daten.

### 2026-08-10 – Werkzeugkette und Doku-Konsistenz

- **Testfix:** `tests/test_orchestrator_adaptive_rag_integration.py` war nicht hermetisch
  gegenueber `APP_LOCAL_ONLY`. Unter `pytest tests/` gruen, unter
  `scripts/run_release_quality_gate.py` (erzwingt `APP_LOCAL_ONLY=1`, Zeile 139) rot.
  Autouse-Fixture pinnt die Variable jetzt auf den getesteten Modus.
- **Gate automatisiert:** `.githooks/pre-commit` fuehrt vor jedem bewussten Commit
  `run_release_quality_gate.py --mode deterministic` aus (~65 s). Autosave-Commits sind
  ueber `HOMEBOT_AUTOSAVE=1` ausgenommen — das Sicherheitsnetz muss auch kaputte Staende sichern.
  Aktiviert via `git config core.hooksPath .githooks`.
- **Sicherheitsnetz:** `scripts/autosave_watcher.ps1` snapshottet den Arbeitsstand alle
  10 Minuten (Autostart bei Anmeldung). Ersetzt die manuelle Backup-Pflicht nicht, sondern
  ergaenzt sie; Backups liegen jetzt ausserhalb des Repos unter `~\homebot_backups\`.
- **Widersprueche aufgeloest:** venv (`venv_bot_20260802` ist produktiv, `venv_mistral_gguf`
  nur Rollback) in AGENTS.md, 00, 06 und PROMPT_STANDARD; Rolle von `ARCHITECTURE.md`
  (Legacy) im Root-README; KG-Bewertung 3/7 vs. 7/7 als Verdrahtung-vs-Implementierung
  erklaert; CommunityDetector in 02 §10 als "nicht verdrahtet" korrigiert.
- **Frische-Marker** in 02, 04, 06, 14, 15, PROMPT_STANDARD und RTX4090-Guide ergaenzt
  (Datum jeweils aus dem im Dokument angegebenen Stand, nicht auf heute gesetzt).
- **Neu:** `01_ARCHITECTURE_DEEP_DIVE.md` §8a — Modul-Landschaft (Psycho- und RAG-Ebenen
  sind geschichtet, nicht doppelt; `refactored_gui/` ist bis auf `quality_dashboard.py` tot).

### 2026-08-01 – SOTA-Doku-Konsolidierung
- `07_KG_SOTA_ANALYSIS.md` in `02_SOTA_ROADMAP.md` integriert (neue Abschnitte 8–10: KG-SOTA-Bewertung, Beyond-SOTA, References)
- `07_KG_SOTA_ANALYSIS.md` nach `docs/09_archived/` verschoben
- `17_WEB_RAG_SOTA_ASSESSMENT.md`-Referenz entfernt (Datei existiert nicht, Inhalt in `02_SOTA_ROADMAP.md` §2 enthalten)
- Adaptive-RAG Integrationsstatus in `02_SOTA_ROADMAP.md` §3 dokumentiert

### 2026-08-01 – Adaptive-RAG Integrationsstatus dokumentiert
- Workdoc 17 um Integrationsstatus erweitert: Orchestrator-Integration OFFEN (~6.5h), Path-Scorer NICHT STARTET (~8h), GNN-ER NICHT STARTET (~4 Tage)
- Status korrigiert: ANALYSE ABGESCHLOSSEN · IMPLEMENTIERUNG PARTIELL
- Empfehlung: Orchestrator-Integration als Priorität 1 (niedriges Risiko, sofortiger Nutzen)

### 2026-07-31 – Adaptive-RAG Pipeline implementiert
- `agent/adaptive_rag.py`: AdaptiveRAGRouter (LLM shallow/deep Classifier), MultiHopRetriever (BFS, max 3 Hops), AdaptiveRAGPipeline (End-to-End)
- 26 Tests PASS (`tests/test_adaptive_rag.py`): Router, MultiHop, Pipeline, Integration
- Multi-Hop-Lücke geschlossen: 4.0 → 8.5/10, Query-adaptive Retrieval: 5.0 → 9.0/10
- **Gesamt-Score: 7.2 → 8.1/10 (SOTA-Niveau erreicht)**

### 2026-07-31 – Web/RAG SOTA-Assessment
- Vollständige SOTA-Analyse von Websearch und RAG-Pipeline gegen Forschungsstand (DOTRAG arxiv 2605.18760, Adaptive-RAG arxiv 2403.14403, GNN-RAG ACL 2025)
- Ergebnis: Websearch 9.5/10 (SOTA), Cross-Encoder 9.0/10 (SOTA), Multi-Hop 4.0/10 (kritische Lücke — seit geschlossen)

### 2026-07-31 – WORKDOC_TEMPLATE SOTA-Erweiterung
- Template um 9 Sektionen erweitert: Definition of Done, Alternativen & Entscheidung (5-Kriterien-Matrix), Abhängigkeiten & Stakeholder, Offene Fragen (Owner+Deadline), Risiko & Impact-Matrix, Sicherheits-/PII-Implikationen, Rollback-Strategie, Token-Budget, Reviewer-Feld
- PROMPT_STANDARD.md erstellt: synchronisiertes Standard-Prompt (Schritt 4 aktualisiert)
- Quellen: Google Design Doc, AWS RFC, Meta WDD, Linux Kernel RFC, AGENTS.md

### 2026-07-26 – Psycho-Session-Persistenz
- Session-Existenzprüfung DB-autoritativ gemacht und synthetische Identity-Recovery entfernt
- Alle produktiven Psycho-Handler auf `add_message_with_result()` und explizites Session-Rebinding migriert
- User-/Assistant-Persistenz als Gate für Generierung, Erfolgslog und Rerun durchgesetzt
- FK-Parent-Check und Interaction-Insert in eine `BEGIN IMMEDIATE`-Transaktion verschoben
- Mood-Progression auf die kanonische Zeitspalte `session_interactions.created_at` korrigiert
- 10 fokussierte und 69 breitere Psycho-Tests bestanden

### 2026-07-25 – SOTA Chat-Streaming
- Typisierten, request-lokalen Event-Stream fuer SIMPLE, PLAN_EXECUTE, REACT, VISION und CACHE eingefuehrt
- Native llama.cpp-Deltas im SIMPLE-Pfad bis `st.write_stream()` durchgereicht
- Cancellation, Iterator-Cleanup, History-Rollback und completion-only SQLite-Persistenz umgesetzt
- REACT-Ausgabe hinter Citation-, Verification- und PII-Gates gehalten; private Denkmarker werden inkrementell gefiltert
- DE/EN/BG-Streamingtexte vereinheitlicht und lokales Gemma-12B/RTX4090-Canary verifiziert

### 2026-07-14 – Doku-SOTA-Audit (LLM-Lesbarkeit)
- `AGENTS.md` (Root) nach agents.md-Standard eingeführt – Agent-lesbarer Einstieg
- Doc-Map in 00_CONTEXT_MASTER vervollständigt (06, funktionen.md, RTX4090-Guide, Archiv-Pfade)
- Faktenfehler in 06 behoben: n_batch 8192→3072 (Code-verifiziert), veraltete Root-Doku-Tabelle, korrupter Text
- Toter Verweis `docs/LLM_GPU_TUNING_BENCHMARK.md` → `scripts/benchmark_llm_gpu_tuning.py`
- Workdoc-/Inventar-Altlasten aus aktivem docs/ nach `docs_archive/` verschoben

### 2026-07-13 – Dokumentation bereinigt
- Reine Arbeitsdokumente gelöscht (ANALYSE_OFFENE_PUNKTE, IMPLEMENTATION_PLAN, I18N_BULGARIAN_COMPLETE_ANALYSIS, VERIFICATION_MANAGER_DEBUG, FUNKTIONEN_ANALYSE_ARBEIT, SOTA_IMPLEMENTATION_PROGRESS)
- Backup-Dateien (.bak_*) bereinigt (19 Dateien)
- CLEANUP.md nach docs/09_archived/ verschoben
- 7 Kern-Dokumente als aktuelle Dokumentation bestätigt

### 2026-07-13 – Dokumentation konsolidiert (Update)
- Alle Dokumentation auf 7 Kern-Dokumente konsolidiert
- Context Engineering SOTA Guide erstellt (06_CONTEXT_ENGINEERING_SOTA.md)
- Archiv für historische Dokumente erstellt
- 00_CONTEXT_MASTER.md als primärer LLM-Kontext eingeführt

### 2026-06-16
- RAG-First-Gating im Orchestrator gehärtet
- ReAct-Agent produktiv verdrahtet
- Semantischer Execution-Router: SIMPLE | PLAN_EXECUTE | REACT

### 2026-06-15
- KG semantic entity match gehärtet
- Invalid Embedding-Payloads werden verworfen

### 2026-06-07
- GPU-Tuning-Default konsolidiert (n_batch=3072)
- Finance-Chat-Result-Contract erweitert
- CRAG-Self-Correction integriert