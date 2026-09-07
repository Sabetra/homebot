# 05 – Developer Guide
<!-- last-verified: 2026-08-02 -->

> **Stand:** 2026-07-13 | **Consolidation Release 1.0**

---

## 1. Environment Setup

### 1.1 Prerequisites
- Python 3.12
- Windows 11 (tested) / Linux compatible
- RTX 4090 or similar GPU (recommended for local LLM)
- 64 GB RAM (recommended)

### 1.2 Virtual Environment
```powershell
# Activate environment
.\venv_bot_20260802\Scripts\Activate.ps1

# Install the validated CUDA baseline before the constrained runtime
python -m pip install -r requirements-native-cu124.txt
$env:CMAKE_ARGS = "-DGGML_CUDA=on;-DCMAKE_CUDA_ARCHITECTURES=89"
python -m pip install -c constraints-win-py312.txt -r requirements.txt
python -m pip install -c constraints-win-py312.txt -r requirements-dev.txt
```

`venv_bot_20260802` is the validated production environment. Keep
`venv_mistral_gguf` unchanged as the rollback environment until the next
planned environment migration.

`requirements.txt` is the direct local production contract.
`constraints-win-py312.txt` is the tested transitive Windows/Python 3.12
matrix. Optional Azure OCR dependencies live in
`requirements-optional-cloud.txt` and are not installed by default.

Regenerate the constraints only after intentional updates:

```powershell
$env:PIP_CONFIG_FILE = "$PWD\config\pip-pypi-only.ini"
python -m piptools compile --no-emit-index-url --resolver backtracking --strip-extras --output-file constraints-win-py312.txt requirements.txt
```

### 1.3 Environment Variables
Set in `.env` or system environment:
```
MODEL_PATH=path/to/gguf/model
DEFAULT_LOCALE=de
```

Die tatsaechlich unterstuetzten Variablen sind im jeweiligen lesenden Codepfad dokumentiert. Nicht implementierte Schalter wie `FINANCE_CACHE_ENABLED` duerfen nicht vorausgesetzt werden.

---

## 2. Running the Application

### 2.1 Start Commands
```powershell
# Full startup with finance
.\start_private_with_finance.ps1

# Public mode without finance
.\start_public_no_finance.ps1

# Fixed startup
.\start_bot_fixed.ps1
```

### 2.2 Development Server
```bash
streamlit run enhanced_streamlit_bot.py
```

---

## 3. Testing

### 3.1 Run Tests
```bash
pytest tests/ -v
pytest tests/ -k test_name --tb=short
```

### 3.2 Release Quality Gates

```powershell
# Gesamter harter Gate: Tests + Fixtures + lokale Gemma4-Canaries
python scripts/run_release_quality_gate.py --mode all --force-regenerate

# Schneller Gate ohne Modellladung
python scripts/run_release_quality_gate.py --mode deterministic

# Nur lokale Gemma4-Canaries (Finance + Profile Synthesis)
python scripts/run_release_quality_gate.py --mode live --model-id gemma-4-12b-it
```

Alle Modi erzwingen `APP_LOCAL_ONLY=1`, verwenden den aktiven Python-Interpreter und schreiben JSON-Berichte unter `monitoring/release_quality/`. Ohne `--keep-going` bricht der Runner nach dem ersten fehlgeschlagenen Schritt ab.

### 3.3 RAG Evaluation
```bash
python ragas_sota_evaluation.py
```

---

## 4. Code Structure Quick Reference

| Directory | Purpose |
|-----------|---------|
| `agent/` | Core AI pipeline |
| `finance/` | Financial query module |
| `i18n/` | Internationalization |
| `wellbeing_session/` | Session management |
| `utils/` | Shared utilities |
| `docs/` | Documentation |

---

## 5. Debugging

### 5.1 Common Issues
- **Model loading fails:** Check MODEL_PATH and GPU memory
- **Translation missing:** Check locale JSON files
- **DB connection errors:** Verify SQLite paths

### 5.2 Logs
Check console output and log files in the project root.

---

*Für Developer-Änderungen, dieses Dokument aktualisieren.*