<!-- last-verified: 2026-09-04 -->
# AGENTS.md

> Agent-Einstieg für dieses Repository (Standard: agents.md).
> Für Projekt-Orientierung IMMER zuerst [docs/00_CONTEXT_MASTER.md](docs/00_CONTEXT_MASTER.md) lesen.

## Projekt

Local-First Multimodal AI Chatbot (Windows 11, 64 GB RAM, Dual-GPU: RTX 4090 24 GB [LLM] + RTX 3060 Ti 8 GB [AUX]).
Primäres Runtime-LLM: Gemma4 12B (GGUF via llama-cpp-python). Streamlit-UI, RAG (FAISS + Docling),
Knowledge Graph (NetworkX), Finance-Query-Engine, Wellbeing-Session-Modul, i18n (DE/EN/BG).

## Setup & Kommandos

```powershell
# Virtuelle Umgebung (IMMER diese verwenden)
# Validierte Produktivumgebung seit 2026-08-02:
<PROJEKT_ROOT>\venv_bot_20260802\Scripts\Activate.ps1
# venv_mistral_gguf bleibt unveraendert als Rollback-Umgebung -- nicht fuer neue Arbeit.

# Tests (immer über das Projekt-venv; vermeide system Python)
# Produktiv-Umgebung aktiviert: <PROJEKT_ROOT>\venv_bot_20260802\Scripts\Activate.ps1
# Fallback/Rollback: <PROJEKT_ROOT>\venv_mistral_gguf\Scripts\Activate.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 tests/ -q --no-header -p no:cacheprovider

# Einzelne Suite
powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 tests/test_finance_reflector_adversarial.py -v

# Security-Scan (privacy-preserving, lokal)
python scripts/dependency_vulnerability_scanner.py
python scripts/dependency_vulnerability_scanner.py --strict   # CI/CD (Exit 1 bei ANY vuln)

# Lizenz & Compliance (AGPL-3.0; Frische + Policy, ~1 s)
python scripts/generate_licenses.py           # nach Install/Remove von Dependencies
python scripts/check_licenses.py              # normal (Frische + Policy)
python scripts/check_licenses.py --strict     # Pre-Commit/CI: blockt bei unknown/needs-review

# Websearch (privacy-first, DuckDuckGo, lokal, SOTA-hardened)
python scripts/agent_websearch.py "Suchanfrage"
python scripts/agent_websearch.py "Aktuelles" --news --max-results 5
python scripts/agent_websearch.py "Query" --json --no-cache   # maschinell lesbar
python scripts/agent_websearch.py "Query" --enrich            # HTML-Snippet-Fetch
python scripts/agent_websearch.py --clear-cache               # Cache leeren
# SOTA: Exponential Backoff (429/403/202), PII-Sanitization, Domain-Blacklist,
#   Graceful Engine-Fehler-Handling (DNS suppressed), TTL-Cache (30min/5min News)

# Websearch via MCP-Tool (privacy-first, DuckDuckGo, lokal, SOTA 2026-07-31)
#   Konfiguriert in cline_mcp_settings.json -> server "websearch"
#   Tool-Namen:
#     - websearch(query, max_results, news, enrich, use_cache) → URLs finden
#     - fetch_content(url, max_chars) → HTML→Text (trafilatura, lokal)
#   Server: <Cline-MCP-Verzeichnis>\websearch\server.py (MCP v2, asyncio)
#   Kein Cloud-LLM, keine API-Keys, Cache unter %LOCALAPPDATA%\ddgs_cache\
#   fetch_content: Domain-Allowlist mit Parent-Domain-Matching, TTL-Cache 30min, max 15K chars
#     Parent-Domain-Matching: "pydantic.dev" in Allowlist → "docs.pydantic.dev" automatisch erlaubt
#     Nur Parent-Domain eintragen, Subdomains werden automatisch abgedeckt (12/12 Tests PASS)
#   Flow: websearch("topic") → [URLs] → fetch_content(url) → [Text]
#   Reactive Pattern: Ergebnisse in <thinking> bewerten → bei unzureichenden
#     Ergebnissen Query reformulieren → erneute Suche (max 2 Reformulierungen)
#     Konvergenz: ≥3 relevante Ergebnisse, verschiedene Quellen, Aspekt abgedeckt
#
#   Websearch-Einsatz im Programmier-Workflow:
#   - Vor Code-Schreiben bei unsicheren API-Calls: websearch → fetch_content → Code
#   - Bei neuen Library-Features: offizielle Doku via fetch_content lesen
#   - Bei unbekannten Fehlern: websearch(error message) → StackOverflow/GitHub Issues
#   - Bei SOTA-Entscheidungen: websearch → arxiv.org/paperswithcode.com → fetch_content
#   - Allowlist-Erweiterung: Bei "Domain not in allowlist"-Fehler neue Parent-Domain
#     in server.py hinzufügen (Subdomains werden automatisch abgedeckt)

# App starten
.\start_private_with_finance.ps1    # mit Finance-Modul
.\start_public_no_finance.ps1       # ohne Finance (public)
```

## Dokumentations-Hierarchie (progressive disclosure)

1. [docs/00_CONTEXT_MASTER.md](docs/00_CONTEXT_MASTER.md) — Master-Kontext, immer zuerst (~3.5K Tokens)
2. [docs/README.md](docs/README.md) — Index aller Kern-Dokus (00–06)
3. Modul-Dokus gezielt bei Bedarf:
   - Bei Orchestrator/SOTA-Pipeline: zuerst `funktionen.md` + `docs/01_ARCHITECTURE_DEEP_DIVE.md`
   - Bei KG-Änderungen: zuerst `docs/02_SOTA_ROADMAP.md` (§8–10: KG-SOTA) + `docs/14_KG_COMMUNITY_DETECTION_IMPLEMENTATION.md`
   - Bei Finance-Änderungen: zuerst `docs/03_FINANCE_MODULE.md`
   - Bei Wellbeing-Modul-Änderungen: zuerst `docs/08_WELLBEING_MODULE_OPTIMIZATION.md`
   - Bei i18n-Änderungen: zuerst `docs/04_I18N_GUIDE.md`
   - Bei GPU/LLM-Parametern: zuerst `docs/RTX4090_RYZEN9_GUIDE.md`
4. [funktionen.md](funktionen.md) — Kompendium großer/komplexer Funktionen (vor Änderungen an Orchestrator/Pipeline lesen und danach pflegen)
5. Archive (`docs/09_archived/`, `docs_archive/`, siehe [ARCHIVE_INDEX.md](ARCHIVE_INDEX.md)) — nur für Historie, nie als aktuelle Quelle
6. Jede Doku trägt `<!-- last-verified: YYYY-MM-DD -->` im Header; bei Abweichung >30 Tagen: als "möglicherweise veraltet" flaggen

## Konventionen

- Python 3.x, Pydantic v2 (KEIN v1-API, kein `__get_validators__`), Pytest, mypy (mypy.ini).
- Code ist Source of Truth; Dokus liefern Kontext. Bei Widerspruch: Code prüfen, Doku fixen.
- Root-Cause-Fixes statt Workarounds; keine silent-fallbacks (try/except um Schema-/Tabellen-Zugriffe ist ein Code-Smell).
- LLM-Special-Tokens nie hardcoden — Single Source of Truth sind GGUF-Metadaten.
- CUDA-Zugriffe nur unter den bestehenden CUDA-Locks; GPU-Parameter nicht "optimistisch" erhöhen.
- i18n: Alle User-facing Strings über `i18n/i18n_manager.py`, Locales DE/EN/BG.
- **Datenbank-Pfade IMMER über `utils/db_path_resolver` beziehen** (`get_db_path()` bzw.
  die `get_*_path()`-Helfer) — niemals Literal-Pfade wie `"rag_store.db"` oder
  `os.path.join(workspace_root, ...)`. Alle produktiven DBs liegen unter dem
  `.db_root`-Ziel (z. B. `~\.local\share\homebot_dbs`), nicht im Repo.
  Hintergrund: Resolver-Umgehungen haben 2026-07/08 dieselbe DB in zwei
  Wurzelverzeichnissen mit divergierendem Inhalt erzeugt (behoben 2026-08-10).
- **Lizenz & Compliance:** Projekt ist **AGPL-3.0** (Copyright Michaël Artebas). Neue
  Abhängigkeiten nur permissiv oder AGPL-kompatibler Copyleft. Nach jeder
  Dependency-Änderung: `python scripts/generate_licenses.py` und
  `python scripts/check_licenses.py --strict` (muss grün sein). Details:
  [docs/19_LICENSES_AND_COMPLIANCE.md](docs/19_LICENSES_AND_COMPLIANCE.md).
  Modell-Gewichte (Gemma etc.) unterliegen ihren eigenen Bedingungen und sind
  NICHT Teil der Repository-Lizenz – nie weiterverteilen.

## GPU-/LLM-Parameter (verifiziert, nicht erhöhen)

| Parameter | Wert | Hinweis |
|-----------|------|---------|
| n_batch | 3072 | 8192 löst ggml-cuda Kernelfehler aus (siehe docs/RTX4090_RYZEN9_GUIDE.md) |
| n_ubatch | 2048 | |
| n_threads / n_threads_batch | 12 | |
| n_gpu_layers | -1 | Voll-Offload auf RTX 4090 |

Overrides nur für Benchmarks via ENV `LLM_N_BATCH` / `LLM_N_UBATCH` (`scripts/benchmark_llm_gpu_tuning.py`).

## Dual-GPU-Platzierung (2026-08-25)

**Single Source of Truth: `utils/gpu_devices.py`** (`get_placement()`) — niemals `cuda:0`/`cuda:1`
hardcoden; Konsumenten nutzen die Placement-Felder.

| Rolle | GPU | CUDA-Runtime | NVML (nvidia-smi) |
|-------|-----|--------------|-------------------|
| LLM (Gemma4 12B, llama.cpp) | RTX 4090 (24 GB) | `cuda:0` | **NVML 1** |
| AUX (Reranker, Embeddings, NLI, OCR, Docling) | RTX 3060 Ti (8 GB) | `cuda:1` | **NVML 0** |

⚠️ **CUDA- und NVML-Index sind auf diesem System vertauscht** — Auflösung erfolgt UUID-basiert
in `gpu_devices.py`; Monitoring muss `llm_nvml`/`aux_nvml` verwenden, nie die Positionsnummer.

- Device-Formen: ONNX → `CUDAExecutionProvider(device_id=aux_cuda)`; Torch/SentenceTransformer/
  EasyOCR/Docling → `aux_device_string` (`"cuda:1"`); CPU-Fallback bleibt überall aktiv.
- **LM Studio ist das LLM-Backend dieses Agents**: `LM Studio.exe` + `llama-server.exe` betreiben
  das lokale LLM von Cline und teilen sich die VRAM beider GPUs — niemals per Kill beenden.
  Vor einer Runtime-Validierung der App (LLM-Load auf der 4090): Agent-Session beenden,
  LM Studio in der GUI schließen (Details: `docs/RTX4090_RYZEN9_GUIDE.md`).
- Overrides: `BOT_LLM_CUDA_DEVICE` / `BOT_AUX_CUDA_DEVICE` (Integer = CUDA-Runtime-Index).
- ONNX-GPU-Reranking braucht `onnxruntime-gpu` im venv (sonst CPU-Reranking, funktional).
- Diagnose: `python -m utils.gpu_devices` · Runtime-Validierung: `python scripts/validate_gpu_placement.py`
- **Validierungsvoraussetzung:** LM Studio schließen (hält VRAM auf beiden GPUs).
- **Selektiver AUX-Modell-Lifecycle (2026-08-28):** Kalte AUX-Modelle (Docling, EasyOCR) werden nach
  Import-/OCR-Peaks via `release_cold_aux_models()` (`utils/aux_model_release.py`) entladen; heiße
  Query-Path-Modelle (Reranker/NLI/Embeddings) bleiben resident; Lazy-Reload bei Bedarf. Details: `funktionen.md` §V.
- **Single-GPU (2026-09-04):** EXAKT eine nutzbare GPU ist voll unterstützt: `gpu_devices.py`
  mappt LLM- und AUX-Rolle auf dasselbe Device (Warnung nur bei expliziten Overrides),
  `scripts/validate_gpu_placement.py` passt mit 1 GPU (LLM+AUX auf derselben GPU: PASS mit
  Warnung; 0 GPUs oder ungültiges Device-String: FAIL). VRAM-Druck wird gemildert: weicher
  VRAM-Precheck (Warnung), OOM-Retry mit KV-Quantisierung, `release_cold_aux_models()`, CPU-Fallback.
  Tests: `tests/test_validate_gpu_placement_single.py`, `tests/test_vram_monitor_single_gpu_role.py`.

## PowerShell-Syntax (Windows 11)

- **KEIN `&` für Command-Chaining** — `&` ist in PowerShell ein Call-Operator, kein Chaining-Operator.
- **Korrekt:** Trenne mehrere Befehle mit **Semikolon `;`**.
  ```powershell
  Copy-Item 'src/file.py' 'backups/file.py.bak'; Copy-Item 'src/file2.py' 'backups/file2.py.bak'
  ```
- **Oder:** Verwende separate `execute_command`-Aufrufe pro Befehl.
- **Oder:** Schreibe ein temporäres `.ps1`-Skript und führe es aus: `powershell -ExecutionPolicy Bypass -File script.ps1`
- **Copy-Item-Syntax:** Immer mit einfachen Anführungszeichen um Pfade mit Leerstellen; Zielverzeichnis muss existieren oder mit `-Force` arbeiten.
- **Beispiel (mehrere Backups):**
  ```powershell
  @('a.py','b.py','c.py') | ForEach-Object { Copy-Item $_ "backups/$_.bak" }
  ```

## Arbeitsweise für Agenten

- **Standard-Prompt:** Bei jedem neuen Auftrag zuerst `docs/PROMPT_STANDARD.md` lesen — enthält das verbindliche Vorgehen (Schritte 1–15) mit Projektkontext, GPU-Grenzen, Testregeln und Doku-Pflicht.
- **Keine Halluzinationen:** Immer die tatsächliche Datei lesen (z. B. via `read_file`), bevor Aussagen über Inhalte gemacht werden. Nicht aus Gedächtnis, vorherigen Sessions oder Vermutungen spekulieren. Bei Unsicherheit: Datei lesen, nicht raten.
- Vor Datei-Überarbeitungen Backup anlegen — **nach `~\homebot_backups\`**, nicht ins Repository.
  Backups innerhalb des Projekts verfälschen Suchergebnisse: Ein Agent findet dann alte
  Dateistände und hält sie für den aktuellen Code.
- **Workdoc:** Bei komplexen Tasks temporäres Workdoc basierend auf `docs/templates/WORKDOC_TEMPLATE.md` erstellen.
- Neue Dokus: in `docs/README.md` eintragen, Nummerierung fortsetzen, in `00_CONTEXT_MASTER.md` referenzieren.
- Große neue/geänderte Funktionen in `funktionen.md` zusammenfassen (Duplikate vorher prüfen).
- Arbeitsdokumente (Workdocs/Worklogs) nach Abschluss löschen oder nach `docs_archive/` verschieben — nie im aktiven `docs/` liegen lassen.
- Tests nach Änderungen ausführen; Warnungen beheben, nicht unterdrücken.

## Dateiintegrität (verbindlich)

Wiederholte Dateikorruption war ein reales Problem. Drei Schichten sichern dagegen ab;
Schicht 1 läuft ohne dein Zutun, Schicht 2 und 3 sind deine Pflicht.

### Schicht 1 — Automatischer Snapshot (kein Handlungsbedarf)

`scripts/autosave_watcher.ps1` committet den gesamten Arbeitsstand alle 10 Minuten
automatisch (Start bei Anmeldung über den Autostart-Ordner). Du musst dafür nichts tun
und kannst es nicht vergessen.

Wiederherstellung einer zerstörten Datei:

```powershell
git -C <PROJEKT_ROOT> log --oneline -- pfad/zur/datei.py     # Verlauf der Datei
git -C <PROJEKT_ROOT> checkout <sha> -- pfad/zur/datei.py    # Stand zurückholen
```

Läuft der Watcher? `Get-Content <PROJEKT_ROOT>\monitoring\autosave.log -Tail 5`

### Schicht 1b — Datenbank-Backup (kein Handlungsbedarf)

Git sichert nur Code; die produktiven DBs sind bewusst gitignoriert (471 MB
Binärdaten gehören nicht in die Historie). Dafür läuft `scripts/db_backup.py`,
angebunden an denselben Watcher: **einmal täglich**, `VACUUM INTO` (konsistent
auch bei laufender App), **7 Tagesstände**, Ziel `~\homebot_backups\db\auto\`.
Der Psycho-Schlüssel wird mitgesichert — ohne ihn wäre die verschlüsselte DB
im Backup wertlos.

Wiederherstellung einer Datenbank:

```powershell
# 1. App stoppen
# 2. Stand waehlen:
Get-ChildItem ~\homebot_backups\db\auto
# 3. Datei zurueckkopieren (Beispiel RAG-Store):
Copy-Item '~\homebot_backups\db\auto\<datum>\rag_store.db' `
          '~\.local\share\homebot_dbs\rag_store.db'
# Bei der Psycho-DB die .key-Datei mitkopieren!
```

Lief es? `Get-Content <PROJEKT_ROOT>\monitoring\db_backup.log -Tail 5`
Was steckt drin? `manifest.json` je Tagesordner (Quellpfade, Größen, quick_check).

**Grenze:** Gleiche Platte. Schützt gegen Softwarefehler, versehentliches
Löschen und Korruption — **nicht** gegen Plattenausfall. Für letzteres wäre
ein zweiter Datenträger nötig.

### Schicht 2 — Nach jedem Schreibvorgang sofort verifizieren

Nach **jedem** Schreiben in eine `.py`-Datei unmittelbar prüfen:

```powershell
python -m py_compile pfad/zur/datei.py
```

Schlägt das fehl, ist die Datei beschädigt. Dann **sofort** zurückrollen (Schicht 1 oder
Backup) und den Schreibvorgang neu ansetzen — nicht weiterarbeiten, nicht "später fixen".
Ein Syntaxfehler, der erst zehn Änderungen später auffällt, ist ungleich teurer.

Bei geänderten JSON-Dateien (z. B. `i18n/locales/*.json`) analog:
`python -c "import json;json.load(open(r'pfad',encoding='utf-8'))"`

### Schicht 3 — Schreibmechanik: niemals ganze Dateien neu ausgeben

Die häufigste Korruptionsursache ist die vollständige Neuausgabe einer großen Datei:
Der Generierungsvorgang bricht ab oder lässt Blöcke weg, und die Datei ist ruiniert.

- **Verboten:** eine bestehende Datei über ~300 Zeilen vollständig neu ausgeben.
- **Stattdessen:** gezielte Ersetzung eindeutig benannter Blöcke (Suchen/Ersetzen auf
  exakt zitiertem Altstand).
- Betrifft besonders die Monolithen: `agent/unified_rag_store.py` (~5.800 Zeilen),
  `agent/orchestrator.py` (~5.200), `agent_chatbot_logic.py` (~4.100),
  `agent/react_agent.py` (~3.900), `wellbeing/wellbeing_db.py` (~3.800).
- Mehrere kleine, je einzeln verifizierte Änderungen sind immer besser als ein großer
  Schreibvorgang.

## Sicherheit

- Local-only Runtime: keine Cloud-LLM-Calls im Produktivpfad.
- PII-Schutz über `pii_protection/`; Finance-Daten bleiben lokal.
- Keine Secrets in Code oder Dokus.
