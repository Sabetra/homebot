# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-08-31

### Added
- English-first `README.md` as public landing page (features, setup, privacy
  model, dual-GPU placement, i18n).
- `CODE_OF_CONDUCT.md` (project code of conduct).
- `CHANGELOG.md` (this file).
- `scripts/check_release_hygiene.py` — deterministic public-repo hygiene gate
  (machine-specific paths, sensitive tracked files, secret patterns,
  required release files).
- `assets/screenshots/` with the capture checklist (`assets/screenshots/README.md`).
- `*.example` templates for machine-specific state (e.g. `.db_root.example`).

### Changed
- `scripts/run_pytest_venv.ps1` and the pre-commit hook now select the first
  *working* virtualenv (production venv first, pytest-import check) instead of
  the first existing one — fixes misleading gate failures caused by a stale
  `.venv`.
- Active docs and scripts now use portable paths (no machine-specific
  `C:\Users\...` references); the two remaining `.gitignore` comment lines
  were made portable as well.
- `AGENTS.md` documentation hierarchy and conventions refreshed for the
  public release.

### Fixed
- `code_executor_engine.py`: the embedded worker template referenced `logger`
  without importing `logging`, which crashed the Plotly fallback path in
  environments without `plotly` installed (4 call sites removed/replaced).
- Plotly fallback now writes errors to `captured_err`, consistent with the
  matplotlib path.

### Security & Privacy
- Local-only runtime: no cloud LLM calls on the production path.
- `settings.json`, `monitoring/`, `.continue/`, `data/`, virtualenvs and all
  machine-specific runtime state are git-ignored and untracked.
- `feedback_data/user_feedback.json` is a PII-free sample only.
- PII protection via `pii_protection/`; finance data stays local.
