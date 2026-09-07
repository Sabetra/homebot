<!-- last-verified: 2026-08-30 -->

# Contributing

Danke für dein Interesse. Dieses Projekt ist ein **Local-First, local-only**
Workspace (Copyright: Michaël Artebas, AGPL-3.0). Bei Änderungen gelten strenge
lokale Qualitätsgates. Lies zuerst [`AGENTS.md`](AGENTS.md) und
[`docs/00_CONTEXT_MASTER.md`](docs/00_CONTEXT_MASTER.md).

## Grundregeln
- **Local-only:** Keine Cloud-LLM-Calls im Produktivpfad, keine Telemetrie.
- **Root-Cause statt Workaround.** Keine silent-fallbacks (try/except um
  Schema-/Tabellen-Zugriffe ist ein Code-Smell).
- **Keine Halluzinationen:** Vor Aussagen über Inhalte die tatsächliche Datei
  lesen. Nicht aus Gedächtnis oder Vermutung spekulieren.
- **i18n:** Alle User-facing Strings über `i18n/i18n_manager.py` (DE/EN/BG).
- **DB-Pfade:** Immer über `utils/db_path_resolver` – nie Literal-Pfade.
- **GPU:** CUDA-Zugriffe nur unter den bestehenden CUDA-Locks; Parameter nicht
  „optimistisch" erhöhen (n_batch=3072, siehe `docs/RTX4090_RYZEN9_GUIDE.md`).

## Setup
```powershell
# Immer die Produktiv-Venv verwenden
<PROJEKT_ROOT>\venv_bot_20260802\Scripts\Activate.ps1

pip install -r requirements.txt
pip install -r requirements-dev.txt   # optional, Dev/Doku-Stack
```

## Entwicklung & Gates
1. **Änderung** gezielt vornehmen (kleine, einzeln verifizierte Blöcke).
2. **Nach jedem `.py`-Schrieb sofort verifizieren:**
   ```powershell
   python -m py_compile pfad/zur/datei.py
   ```
3. **Tests:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 tests/ -q --no-header -p no:cacheprovider
   ```
4. **Lizenz-Gate** (bei Änderungen an Abhängigkeiten):
   ```powershell
   python scripts/generate_licenses.py
   python scripts/check_licenses.py --strict
   ```
5. **Commit** – der Pre-Commit-Hook (`.githooks/pre-commit`) führt das
   Lizenz-Gate + deterministische Release-Gate (ca. 65 s) aus und blockt bei
   Rot. Autosave (`HOMEBOT_AUTOSAVE=1`) wird nie blockiert.

## Dateiintegrität
- **Niemals** eine Datei > ~300 Zeilen vollständig neu ausgeben (Korruptionsrisiko).
  Stattdessen gezielte Suchen/Ersetzen auf exakt zitiertem Altstand.
- Backups vor Überarbeitungen nach `~\homebot_backups\` – **nicht** ins Repo.
- Automatischer Snapshot: `scripts/autosave_watcher.ps1` (alle 10 min).

## Doku-Pflicht
- Neue/geänderte Funktionen in `docs/` (nummeriert) + `docs/README.md` +
  `00_CONTEXT_MASTER.md` eintragen.
- Große Funktionen in `funktionen.md` zusammenfassen (Duplikate prüfen).
- Workdocs nach Abschluss löschen oder nach `docs_archive/` verschieben.

## Lizenz & Compliance
- Projekt: **AGPL-3.0** (Copyright: Michaël Artebas).
- Neue Abhängigkeiten: Lizenz muss permissiv oder AGPL-kompatibler Copyleft
  sein (siehe `docs/19_LICENSES_AND_COMPLIANCE.md`).
- Nach Änderung: `LICENSES.md` neu generieren + `--strict`-Gate grün halten.

## Code of Conduct
Beiträge folgen dem [Code of Conduct](CODE_OF_CONDUCT.md).
