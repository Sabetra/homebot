<!-- last-verified: 2026-08-31 -->

# Support

Dies ist ein **Local-First, local-only** Projekt (Copyright: Michaël Artebas,
AGPL-3.0). Es gibt keinen externen Support-Dienst, keine Cloud-Tickets und
keine Telemetrie. Fragen und Bug-Reports laufen über **GitHub Issues**;
sicherheitsrelevante Meldungen siehe [SECURITY.md](SECURITY.md).
Support bedeutet hier: das System selbst diagnostizieren, Logs lesen und
die lokale Doku konsultieren.

## Wo ansetzen

| Problem | Erste Anlaufstelle |
|---------|--------------------|
| App startet nicht / GPU | `docs/RTX4090_RYZEN9_GUIDE.md`, `python -m utils.gpu_devices` |
| Orchestrator / Pipeline | `docs/01_ARCHITECTURE_DEEP_DIVE.md`, `funktionen.md` |
| Lizenzen / Compliance | `docs/19_LICENSES_AND_COMPLIANCE.md`, `LICENSES.md` |
| Tests / Release-Gate | `docs/05_DEVELOPER_GUIDE.md`, `scripts/run_release_quality_gate.py` |
| Agent-Workflow | `AGENTS.md`, `docs/PROMPT_STANDARD.md` |

## Diagnose-Befehle (lokal)

```powershell
# Eigene virtuelle Umgebung aktivieren
# (z. B. .\venv\Scripts\Activate.ps1 — Setup: docs/05_DEVELOPER_GUIDE.md)

# Syntax-Check der Kernmodule
python -m py_compile enhanced_streamlit_bot.py agent/orchestrator.py agent_chatbot_logic.py

# Test-Suite
powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 tests/ -q --no-header -p no:cacheprovider

# Lizenz-Gate
python scripts/check_licenses.py --strict

# GPU-Platzierung
python -m utils.gpu_devices
```

## Logs & Monitoring
- Release-Berichte: `monitoring/release_quality/` (projekt-relativ, git-ignoriert)
- Autosave-Status: `Get-Content monitoring\autosave.log -Tail 5`
- DB-Backup: `Get-Content monitoring\db_backup.log -Tail 5`

## Wichtige Hinweise
- **Kein externer Support-Dienst.** Alle Diagnosen laufen lokal;
  Fragen und Bug-Reports über GitHub Issues.
- **Keine PII/Finance-Daten nach außen senden** – das Projekt ist local-only
  by design. Bei der Fehleranalyse nur lokal gespeicherte Logs verwenden.
- **Modelle/Gewichte** unterliegen ihren eigenen Bedingungen (Gemma Terms of
  Use etc.) – nicht an Dritte weitergeben, siehe
  `docs/19_LICENSES_AND_COMPLIANCE.md` §5.

## Reproduzierbarer Bug-Report
GitHub-Issue mit folgenden Angaben öffnen (sicherheitsrelevante
Schwachstellen **nicht** öffentlich — siehe SECURITY.md):
1. Exakte Schritte + Erwartung vs. Ist.
2. Venv + Python-Version: `python --version`
3. GPU-Zustand: `python -m utils.gpu_devices`
4. Relevanter Log-Ausschnitt (PII redigieren).
5. Betreffende Datei/Funktion, falls bekannt.
