# Implementation Plan — Public GitHub Launch (v1.0.0)

> **Datum:** 2026-08-31 · **Status:** in Ausführung (Code-Seite) / offen (User-Seite)
> **Workdoc:** [`docs/WORKDOC_PUBLIC_LAUNCH_20260831.md`](docs/WORKDOC_PUBLIC_LAUNCH_20260831.md)

## Ziel

Das Repository ist technisch fertig; der Launch-Blocker sind Public-Readiness-Gaps.
Dieser Plan schließt sie ab, bis das Repo sicher und professionell auf GitHub
veröffentlicht werden kann.

## Entscheidungen (vorab empfohlen, User-Zustimmung ausstehend)

| # | Entscheidung | Empfehlung |
|---|--------------|------------|
| 1 | Push-Strategie | **Orphan-Branch `release/v1.0.0` + Tag `v1.0.0`** — garantiert, dass kein Commit der lokalen Historie (inkl. sensibler Zwischenstände) öffentlich wird. Vorher: `git bundle create homebot-full-history.bundle --all` als lokale Sicherung. |
| 2 | README-Sprache | **EN-first** (OSS-Standard), deutsche/englische/bulgarische Doku über Docs-Links. |
| 3 | Screenshots | **Playwright-Skript für statische UI** (Finance, Psych, RAG, Settings) + optional 1 Live-LLM-GIF (braucht VRAM-Freigabe + LM-Studio-Down). |
| 4 | CI | **Minimales GitHub-Actions-Workflow** (Python 3.12, pytest, Lizenz-Check). |

## Stufen

### Stage 0 — Sicherheit & Baseline
- Git-Status sauber, Autosave-Watcher aktiv, venv bestätigt.

### Stage 1 — Repository-Hygiene
- `.gitignore`: `.db_root`, `monitoring/`, `data/websearch_cache.json`, `settings.json`,
  `.continue/`, `_search_tmp.txt`, `marketing/` ergänzen.
- `git rm --cached` (bleibt lokal): `.db_root`, `config/path_allowlist.json`, `settings.json`,
  `_search_tmp.txt`, `monitoring/`, `data/websearch_cache.json`, `.continue/`.
- Examples: `.db_root.example`, `config/path_allowlist.example.json`.
- `docs/WORKDOC_CODEBASE_AUDIT_20260828.md` → `docs/09_archived/`.
- `feedback_data/user_feedback.json` bleibt als Beispieldatensatz (PII-geprüft).
- Verifikation: `git ls-files`-Check + Autosave-Log + Commit.

### Stage 2 — Compliance-UI
- i18n-Keys `finance_ui.main.disclaimer` + `wellbeing.disclaimer` (DE/EN/BG).
- Sichtbare Disclaimers in `finance/tab.py` (`st.info`) und
  `wellbeing_session_interface.py` (Banner in `render_complete_interface`).
- `SECURITY.md`: öffentliche Schwachstellen-Meldung (GitHub Issues, Security-Policy).
- `SUPPORT.md`: öffentlicher Support-Pfad (Issues) + lokale Diagnose.
- Test: `tests/test_disclaimer_i18n.py` (Keys + Strings + Fallback).

### Stage 3 — Portable Pfade
- `utils/model_registry.py`: Default = `~/.cache/lm-studio/models/lmstudio-community`.
- `scripts/model_loader.py`: Modell-Pfade aus `models_root()` abgeleitet (Env-Override bleibt).
- `agent/tool_schemas.py`: Beispielpfad anonymisiert.
- `.githooks/pre-commit`: Repo-Root relativ, Venv-Autodetection.
- `start_*.ps1`: `$PSScriptRoot` + Venv-Autodetection.
- `scripts/db_backup.py`: `HOMEBOT_BACKUP_ROOT`-Env-Override.
- `config/user_id_config.py`: Pfad via `utils.db_path_resolver` (AGENTS.md-Konvention).
- Doku: `AGENTS.md`, `CONTRIBUTING.md`, `DEVELOPER_QUICK_START.md`,
  `docs/00_CONTEXT_MASTER.md`, `docs/05_DEVELOPER_GUIDE.md`, `SECURITY.md`, `SUPPORT.md`.
- Tests: `tests/test_model_registry_portable.py`, `tests/test_user_id_config_resolver.py`.

### Stage 4 — README-Landing-Page (EN-first)
- Badges, Screenshot-Bereich, Features, Quick Start (Windows + Linux/macOS),
  Architecture, Module Map, Hardware, Legal & Disclaimers, Docs, Community, License.

### Stage 5 — Screenshots (User-Seite, Produktionsmaschine)
- Playwright-Skript `scripts/capture_screenshots.py` + `assets/screenshots/`.
- Ziel-Screens: Chat, Finance, Psychologie, RAG/Import, Settings.
- Optional: Live-LLM-GIF (Voraussetzungen: LM-Studio schließen, VRAM frei).

### Stage 6 — Launch-Vorbereitung
- `CHANGELOG.md` (Keep-a-Changelog).
- `.github/workflows/ci.yml` (pytest + Lizenz-Gate).
- `scripts/check_release_hygiene.py` + `tests/test_release_hygiene.py`.
- `marketing/local_llama_post.md`, `marketing/show_hn_post.md` (intern, git-ignoriert).

### Stage 7 — Finale Gate + Push (User-Seite)
- Vollständiges deterministisches Gate grün (pytest + Lizenz + Hygiene).
- `git bundle` (Historie), Orphan-Branch `release/v1.0.0`, Tag `v1.0.0`.
- Remote setzen, pushen, Topics/Description/License im GitHub-UI konfigurieren.
- Launch-Posts veröffentlichen (r/LocalLLaMA → HN Show, 24 h Abstand).

## Abnahmekriterien (Definition of Done)

1. `git ls-files` enthält keine maschinen-spezifischen Dateien mehr.
2. `git grep -nE 'C:\\\\Users' -- '*.py' '*.ps1' '.githooks'` ist leer (Doku-Ausnahmen bewusst).
3. `tests/test_disclaimer_i18n.py` grün in allen 3 Locales.
4. `scripts/check_release_hygiene.py` Exit 0.
5. Deterministisches Release-Gate (pytest) grün.
6. `python scripts/check_licenses.py --strict` grün.
7. README ohne interne Pfade, EN-first.
8. Stage-5-Checkliste + Push-Anleitung an Sabetra übergeben.
