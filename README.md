<!-- last-verified: 2026-08-31 -->
# Bot6 — Local-First Multimodal AI Assistant

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![UI: Streamlit](https://img.shields.io/badge/UI-Streamlit-orange.svg)](https://streamlit.io/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-green.svg)](tests/)

**Bot6 is a fully local AI assistant for your desktop.** Chat with a local
large language model, search your own documents (RAG), ask questions about
your personal finances, and use a structured psychological support module —
**everything runs on your hardware**. No cloud LLM calls, no telemetry, no
account. Your chats, documents, finances, and psychology data never leave
your machine.

> **Privacy by design.** All inference, retrieval, and storage happen
> locally. Optional cloud integrations exist only behind an explicit opt-in
> flag and are off by default in local-only mode.

## Screenshots

> 📸 **Screenshots folgen noch.** Aufnahme-Spezifikation & PII-Regeln:
> [`assets/screenshots/README.md`](assets/screenshots/README.md).
> Sobald `chat.png`, `finance.png`, `psychology.png` und `settings.png`
> vorhanden sind, wird die Bildtabelle unten aktiviert.

<!--
| Chat | Finance | Psychology | Settings |
|------|---------|------------|----------|
| ![Chat](assets/screenshots/chat.png) | ![Finance](assets/screenshots/finance.png) | ![Psychology](assets/screenshots/psychology.png) | ![Settings](assets/screenshots/settings.png) |
-->

## Features

- 💬 **Local LLM chat** — Gemma 12B (GGUF) via llama.cpp, streamed answers,
  full session persistence (SQLite)
- 📚 **RAG over your documents** — PDF & image ingestion (Docling + EasyOCR),
  FAISS retrieval, Knowledge Graph (NetworkX), reranking
- 💰 **Finance query engine** — natural-language questions over local
  financial data (strictly local; see [disclaimers](#legal--disclaimers))
- 🧠 **Psychological support module** — structured session framework with
  crisis routing (see [disclaimers](#legal--disclaimers))
- 🖼️ **Multimodal** — PDF, images, OCR, local document conversion
- 🌍 **i18n** — German, English, Bulgarian
- 🔒 **Safety model** — PII protection, strict local-only runtime mode
  (`APP_LOCAL_ONLY=1`), deny-by-default network egress
- ⚙️ **Dual-GPU placement** — LLM on one GPU, auxiliary models (embeddings,
  reranker, OCR) on a second; CPU fallback everywhere

## Quick Start

> Tested on **Windows 11** (64-bit). Linux/macOS work with the same Python
> stack — see [docs/05_DEVELOPER_GUIDE.md](docs/05_DEVELOPER_GUIDE.md).

```powershell
# 1. Clone and create a virtual environment
git clone https://github.com/<your-org>/bot6.git bot6
cd bot6
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Get a model (GGUF, e.g. Gemma 12B via LM Studio).
#    Default lookup: ~/.cache/lm-studio/models/  (override: BOT_MODELS_DIR)

# 4. Run the app (finance module enabled)
.\start_private_with_finance.ps1
# — or manually:
streamlit run enhanced_streamlit_bot.py
```

For a strict **offline-only** runtime (blocks web search, remote OCR, and
Hugging Face hub access):

```powershell
$env:APP_LOCAL_ONLY = "1"
streamlit run enhanced_streamlit_bot.py
```

## Architecture at a Glance

```
 Streamlit UI  (Chat · RAG/Documents · Finance · Psychology · Settings)
        │  Pydantic-validated boundaries (schema-first)
 Orchestrator  (agent/orchestrator.py)
        │  tool routing · session state · runtime policy · PII protection
        │
   ┌────┴────────────────────┬───────────────────┐
   │                         │                   │
  LLM                      RAG               Finance + Psychology
 (llama.cpp, GPU)     (FAISS + Docling +    (query engine,
                       Knowledge Graph,      sessions, crisis
                       reranker, GPU/CPU)    routing — local)
```

All databases resolve to a location **outside the repository**
(`~/.local/share/bot6_dbs`, override `BOT6_DB_ROOT`) through a single path
resolver (`utils/db_path_resolver.py`) — the repository itself stays clean
of user data.

## Hardware Notes

| Use case | Recommendation |
|----------|----------------|
| LLM inference | 16–24 GB VRAM GPU (validated: RTX 4090 24 GB with Gemma 12B GGUF) |
| Auxiliary models (embeddings, reranker, OCR, Docling) | Second GPU with 8 GB+ (validated: RTX 3060 Ti) — or the same GPU |
| CPU-only | Works (CPU fallback is built in), slower |

Verified GPU parameters (do not raise blindly): `n_batch=3072`,
`n_ubatch=2048`, `n_threads=12`, full layer offload — details in
[docs/RTX4090_RYZEN9_GUIDE.md](docs/RTX4090_RYZEN9_GUIDE.md).

## Privacy & Security

- **Local-first runtime:** all LLM inference and data storage happen on your
  machine. Optional cloud integrations (remote OCR, web search, URL
  ingestion) only activate behind an explicit opt-in flag.
- **Strict offline mode:** `APP_LOCAL_ONLY=1` enforces deny-by-default
  network egress and Hugging Face offline variables
  (`HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_DATASETS_OFFLINE`) via
  `utils/runtime_policy.py`.
- **PII protection:** a dedicated module (`pii_protection/`) masks sensitive
  data before it enters prompts or logs.
- **Data storage:** all SQLite databases live outside the repository
  (central path resolver, see above) — the repository contains no user data.
- **Reporting vulnerabilities:** see [SECURITY.md](SECURITY.md).

## Configuration

| Setting | Default | Meaning |
|---------|---------|---------|
| `APP_LOCAL_ONLY` | `0` | `1` = strict offline (blocks web search, remote OCR, HF hub) |
| `APP_ENABLE_FINANCE_TAB` | `1` | `0` = hide the Finance tab (public/shared deployments) |
| `BOT6_DB_ROOT` | `~/.local/share/bot6_dbs` | Database root (env or `.db_root` marker file) |
| `BOT_MODELS_DIR` | `~/.cache/lm-studio/models/lmstudio-community` | GGUF model directory |
| `BOT6_USER_ID` | (auto) | Explicit user ID (tests/CI) |
| `LLM_N_BATCH` / `LLM_N_UBATCH` | `3072` / `2048` | Benchmark-only llama.cpp overrides |

Main entry points: Streamlit app `enhanced_streamlit_bot.py` ·
orchestration `agent/orchestrator.py` · RAG store `agent/unified_rag_store.py`
· toolkit `agent_toolkit.py`.

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/00_CONTEXT_MASTER.md](docs/00_CONTEXT_MASTER.md) | Master context — read this first |
| [docs/README.md](docs/README.md) | Full documentation index (00–19) |
| [docs/01_ARCHITECTURE_DEEP_DIVE.md](docs/01_ARCHITECTURE_DEEP_DIVE.md) | Architecture & module entry points |
| [docs/03_FINANCE_MODULE.md](docs/03_FINANCE_MODULE.md) | Finance module |
| [docs/04_I18N_GUIDE.md](docs/04_I18N_GUIDE.md) | Internationalization (DE/EN/BG) |
| [docs/08_PSYCH_MODULE_OPTIMIZATION.md](docs/08_PSYCH_MODULE_OPTIMIZATION.md) | Psychology module |
| [docs/19_LICENSES_AND_COMPLIANCE.md](docs/19_LICENSES_AND_COMPLIANCE.md) | Licensing & compliance |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [SUPPORT.md](SUPPORT.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community |

## Legal & Disclaimers

- **Finance module:** the Finance tab and query engine are for personal
  information management only. They do **not** constitute tax, legal, or
  investment advice. Consult a qualified professional for financial decisions.
- **Psychology module:** the psychological support module is a structured
  self-help/session framework, **not** a medical service, diagnosis, or a
  replacement for professional care. If you are in crisis, contact your
  local emergency services immediately (e.g. **112** in Germany/EU).
- **AI system disclosure (EU AI Act Art. 50(1)):** Bot6 is an AI system.
  In line with the EU AI Act, the app clearly discloses to the user that
  they are interacting with an AI system — a visible banner is shown in the
  wellbeing/session UI in every supported language (DE/EN/BG).
- **Model weights:** not included in this repository. Gemma weights are
  governed by Google's Gemma Terms of Use and must not be redistributed
  here; other models carry their providers' licenses (typically
  Apache-2.0/MIT).
- **License:** AGPL-3.0, see below.

## Development

```powershell
# Tests (the deterministic release gate runs the full suite on every commit)
python -m pytest tests/ -q

# License & compliance (AGPL-3.0; must stay green after dependency changes)
python scripts/generate_licenses.py
python scripts/check_licenses.py --strict

# Compile-time syntax check
python -m py_compile enhanced_streamlit_bot.py agent/orchestrator.py agent/unified_rag_store.py
```

- **Pre-commit gate:** every commit runs the deterministic release gate
  (pytest + license check + profile-fixture evaluation) via
  `.githooks/pre-commit` (bypass: `git commit --no-verify`).
- **Dependency layout:** runtime in `requirements.txt`, dev stack in
  `requirements-dev.txt`.
- **Contributor rules of thumb:** prefer root-cause fixes over error
  swallowing; treat meaningful warnings as migration tasks, not noise;
  keep runtime mode behavior consistent across all entrypoints;
  avoid broad destructive cleanup without dry-run + allowlist.
  Details: [CONTRIBUTING.md](CONTRIBUTING.md).
- **Staged upgrades:** docling (pinned for wrapper compatibility),
  streamlit, llama-cpp-python, langchain/langgraph — always with
  compatibility validation before bumping.

## License

This project is licensed under the **GNU Affero General Public License v3.0
(AGPL-3.0)** — see [`LICENSE`](LICENSE). Copyright: Michaël Artebas.

Third-party dependency licenses are inventoried in [`LICENSES.md`](LICENSES.md)
(generated by `scripts/generate_licenses.py`, checked by
`scripts/check_licenses.py`). Compliance details:
[`docs/19_LICENSES_AND_COMPLIANCE.md`](docs/19_LICENSES_AND_COMPLIANCE.md).

---

Related documents: [`CHANGELOG.md`](CHANGELOG.md) ·
[`CONTRIBUTING.md`](CONTRIBUTING.md) · [`SUPPORT.md`](SUPPORT.md) ·
[`SECURITY.md`](SECURITY.md) · [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
