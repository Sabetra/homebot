# Workdoc: WIP-Testfailures, Autosave-Kette, model_loader.log, MCP-Duplex

> **Erstellt:** 2026-08-20 21:30
> **Status:** ABGESCHLOSSEN
> **Autor:** Cline-Agent (Act-Mode, freigegebener Plan A–F)
> **Reviewer:** WIP-Owner (Krisen-Entscheidung Option B)

---

## Original-Auftrag

Behebe die WIP-Testfailures, die inaktive Autosave-/Reliability-Kette, den
`model_loader.log`-Quirk und die doppelte Cline-MCP-Instanz gemäß dem in
Act-Mode freigegebenen Plan.

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| Embedding-Cache-Env-Override, Krisen-Test (Option B), Watcher-Hardening, model_loader-Logpfad, MCP-Prozesshygiene, Release-Gate | LLM-Canaries (live-Modus), Finance-Pipeline-Änderungen, Cline-Built-in-Server-Konfiguration (UI-Action) |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Embedding-Test honoriert `SENTENCE_TRANSFORMERS_HOME` | `tests/test_embedding_singleton_local_cache.py` grün | ✅ |
| 2 | Krisen-Handler-Test verankert neuen Vertrag (acute → `generate()` mit Krisen-Kontext, keine harte Blockade) | `tests/test_psychological_chat_input_handler.py` 32/32 grün | ✅ |
| 3 | Watcher-Kette aktiv: Snapshot + DB-Backup + Heartbeat, kein Stapeln | `monitoring\autosave.log` "Zyklus ok", frischer git-Autosave-Commit | ✅ |
| 4 | `model_loader.log` CWD-unabhängig in `monitoring\` | Import-Test aus fremdem CWD: keine CWD-Datei | ✅ |
| 5 | Keine duplizierte MCP-Instanz; Server startet mit konfiguriertem Interpreter | Probe: ALIVE nach 8 s | ✅ |
| 6 | Deterministischer Release-Gate grün | Gate-Runner: 633 passed, `gate_passed: true` | ✅ |
| 7 | `py_compile`/Parse-Check aller geänderten Dateien | exit 0, Parser ohne Fehler | ✅ |

## Alternativen & Entscheidung (Krisenpfad)

| # | Option | Pro | Contra | Entscheidung |
|---|--------|-----|--------|--------------|
| A | WIP-Code zurücksetzen, alten Blockier-Vertrag behalten | Test unverändert | Bewusste Neugestellung (begleitete `generate()`-Antwort) wird verworfen | abgelehnt |
| B | Neues Krisen-Design behalten, Test umschreiben | Behält sichereres, empathischeres Verhalten; verankert echten WIP-Zustand | Test-Änderung nötig | **gewählt** (WIP-Owner) |

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|-------|
| 1 | `utils/embedding_singleton.py` honoriert `SENTENCE_TRANSFORMERS_HOME`, Default bleibt `models_cache/` | Datei + grüner Test |
| 2 | `scripts/model_loader.py`: FileHandler auf absolutem Pfad `monitoring\model_loader.log`, Duplikat- + `PermissionError`-Fallback | `scripts\model_loader.py:42–57` |
| 3 | `scripts/autosave_watcher.ps1`: Mutex `Global\bot6-git-autosave-watcher`, Child-Timeout, Heartbeat, Zyklus = `autosave_snapshot.ps1` + `scripts/db_backup.py` (venv 20260802) | Datei + Log "Zyklus ok (1 s)" |
| 4 | MCP-Websearch-Server startet sauber mit `venv_mistral_gguf\Scripts\python.exe` | Probe: ALIVE nach 8 s, sauberes Beenden |
| 5 | Gate-Berichte unter `monitoring\release_quality\20260820_212458\` | `release_gate_20260820_212458.json`: `gate_passed: true` |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `utils/embedding_singleton.py` | Env-Override `SENTENCE_TRANSFORMERS_HOME` honorieren, Default `models_cache/` | Embedding-Test grün |
| 2 | `tests/test_embedding_singleton_local_cache.py` | Erwartung an neuen Cache-Pfad-Vertrag | grün |
| 3 | `tests/test_psychological_chat_input_handler.py` | Option B: Test auf `generate()`-Krisenvertrag umgeschrieben (32 Tests) | 32/32 grün |
| 4 | `scripts/model_loader.py` | Logpfad absolut nach `monitoring\`, Guard gegen Handler-Duplikate | Import-Test fremdes CWD: ok |
| 5 | `scripts/autosave_watcher.ps1` | Mutex, Child-Timeout, Heartbeat-Log, Zyklus Snapshot+DB-Backup | "Zyklus ok" + frischer Autosave-Commit |
| 6 | `funktionen.md` (Krisenpfad), `docs/08_PSYCH_MODULE_OPTIMIZATION.md` | Vertrag neu dokumentiert (acute → begleitete Antwort) | — |
| 7 | Repo-Root | Irrtümliche `model_loader.log` im CWD entfernt (root-cleanup-Commit) | CWD-Test: keine Wiederauffüllung |

## Rollback-Strategie

| Schritt | Aktion | Befehl / Referenz |
|---------|--------|-------------------|
| 1 | Datei-Stand zurückholen | `git log --oneline -- <datei>`; `git checkout <sha> -- <datei>` (Autosave-Snapshots alle 10 min) |
| 2 | DB-Stand zurückholen | `%USERPROFILE%ot6_backups\db\auto\<datum>\` (täglich, `VACUUM INTO`) |
| 3 | Watcher bei Defekt | PID beenden, `scripts\autosave_snapshot.ps1` manuell, Watcher neu starten (Mutex verhindert Stapeln) |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | `pytest tests/ -q` (via Release-Gate, deterministisch) | **633 passed in 65.13 s**, 0 failed | 2026-08-20 |
| 2 | `run_profile_synthesis_eval.py --mode fixture --strict` (Gate-Schritt) | `gate_passed: true`, parse/schema/semantic/match 1.0 | 2026-08-20 |
| 3 | `py_compile` der 4 geänderten .py-Dateien | exit 0 | 2026-08-20 |
| 4 | PowerShell-Parser `autosave_watcher.ps1` | 0 Fehler | 2026-08-20 |
| 5 | CWD-Independence-Import `scripts/model_loader.py` aus `C:\Temp\cline\cwdtest` | keine CWD-Datei, `monitoring\model_loader.log` ok | 2026-08-20 |

## Offene Fragen

| # | Frage | Owner | Status |
|---|-------|-------|--------|
| 1 | `websearch` in Cline zeigt "Not connected" (Server-Kind-Prozess nach Duplex-Beendigung nicht neu gestartet) — **UI-Aktion nötig**: MCP-Seite → Server neu starten oder Cline neu starten. Starttauglichkeit mit konfiguriertem Interpreter ist verifiziert. | User | offen |
| 2 | Falls die Duplizierung nach einem Cline-Neustart zurückkehrt: Ursprung vermutlich Cline-Built-in-Server → in der Cline-UI deaktivieren. | User | offen |
