<!-- last-verified: 2026-08-20 -->
# Developer Quick Start

This guide reflects the current local-first architecture and active entrypoints.

## 1) Setup

Use Python 3.12 and install the validated RTX 4090 / CUDA 12.4 wheels first:

```powershell
python -m pip install -r requirements-native-cu124.txt
$env:CMAKE_ARGS = "-DGGML_CUDA=on;-DCMAKE_CUDA_ARCHITECTURES=89"
python -m pip install -c constraints-win-py312.txt -r requirements.txt
```

For development tooling (tests, linting, docs):

```powershell
python -m pip install -c constraints-win-py312.txt -r requirements-dev.txt
```

Azure Computer Vision is not part of the local-first runtime. Install it only
for the optional cloud OCR integration:

```powershell
python -m pip install -c constraints-win-py312.txt -r requirements-optional-cloud.txt
```

Regenerate the tested Windows/Python 3.12 lock after intentional dependency
updates. The repository-owned pip configuration prevents machine-level indexes
from entering resolver and build-backend subprocesses. `--no-emit-index-url`
keeps credentials and private indexes out of the committed file:

```powershell
$env:PIP_CONFIG_FILE = "$PWD\config\pip-pypi-only.ini"
python -m piptools compile --no-emit-index-url --resolver backtracking --strip-extras --output-file constraints-win-py312.txt requirements.txt
```

VS Code / Pylance interpreter baseline:

- The validated production environment is `<PROJEKT_ROOT>\venv_bot_20260802\Scripts\python.exe`.
- Keep `<PROJEKT_ROOT>\venv_mistral_gguf` unchanged as the migration rollback environment.
- Keep `.vscode/settings.json` aligned (`python.defaultInterpreterPath`, optional `python.analysis.extraPaths`).
- If imports like `llama_cpp` are flagged despite successful terminal import, reload the language server after interpreter changes.

## 2) Run the App

Primary entrypoint:

```bash
streamlit run enhanced_streamlit_bot.py
```

Optional finance-tab disable for shared deployments:

```powershell
$env:APP_ENABLE_FINANCE_TAB = "0"
streamlit run enhanced_streamlit_bot.py
```

## 3) Runtime Mode Model

The application is connected by default.

- APP_LOCAL_ONLY defaults to 0.
- Runtime network egress is blocked (except loopback) when local-only mode is active.
- Web-search and URL-ingestion paths are disabled in local-only mode.
- Hugging Face offline env vars are enforced in local-only mode.

To explicitly force local-only mode:

```powershell
$env:APP_LOCAL_ONLY = "1"
```

To force connected mode in the current session:

```powershell
$env:APP_LOCAL_ONLY = "0"
```

At startup, verify the bootstrap line:

- `[BOOT] Runtime mode resolved: APP_LOCAL_ONLY raw='...' normalized=0|1`

If `normalized=1`, internet tools are intentionally blocked.

Finance tab policy check:

```powershell
Get-ChildItem Env:APP_ENABLE_FINANCE_TAB
```

## 4) Core Files You Will Touch

- UI boot + app wiring: enhanced_streamlit_bot.py
- Agent coordinator: agent/orchestrator.py
- Tool integration: agent_toolkit.py
- Unified retrieval store: agent/unified_rag_store.py
- Main chat logic: agent_chatbot_logic.py
- Document conversion: utils/docling_processor.py
- Runtime policy: utils/runtime_policy.py

## 5) Validation Commands

Run targeted checks first:

```bash
python -m py_compile enhanced_streamlit_bot.py agent_toolkit.py agent/orchestrator.py agent/unified_rag_store.py agent_chatbot_logic.py utils/runtime_policy.py
```

Runtime mode sanity check:

```powershell
Get-ChildItem Env:APP_LOCAL_ONLY
```

Run tests:

```bash
python -m pytest tests/ -q
```

Run profile synthesis fixture evaluation:

```bash
python scripts/run_profile_synthesis_eval.py --mode fixture --strict
```

## 6) Documentation Canonical Sources

- Architecture details: ARCHITECTURE.md
- Local-only and dependency decisions: docs/16_DEPENDENCY_SCANNER.md (aktuelle Doku)
- Cleanup policy (Historie): docs/09_archived/CLEANUP.md; aktive Regeln: AGENTS.md (Dateiintegrität)

## 7) Contributor Rules of Thumb

- Prefer root-cause fixes over error swallowing.
- Treat meaningful warnings as migration tasks, not noise.
- Keep runtime mode behavior consistent across all entrypoints.
- Avoid broad destructive cleanup operations without dry-run + allowlist.
