# Workdoc: Vollst\u00e4ndiges Bot6\u2192Homebot-Rename (Code, Doku, lokale Pfade)

> **Erstellt:** 2026-09-04
> **Status:** IN_ARBEIT
> **Autor:** Cline

## Original-Auftrag

> "ok, dann arbeite die offenen Punkte ab." \u2014 offene Punkte aus der Launch-Session:
> (1) Vollst\u00e4ndiges Bot6\u2192Homebot-Rename \u00fcber Docs/Code (inkl. BOT6_*-Env-Vars),
> (2) echter pip-audit-Scan mit Netzwerk,
> (3) Tippfehler "Saberta" in Commit d4e756a umformulieren.

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| 9 BOT6_*-Env-Vars \u2192 HOMEBOT_* (Code, Tests, Doku, Hooks, PS1) | docs_archive/, docs/09_archived/, datierte WORKDOC_* (Historie) |
| Default-Pfade: bot6_dbs, bot6_backups, ~/.cache/bot6 \u2192 homebot_* | Repo-Ordner C:\\Users\\bot6 (Session-/Autostart-Pfade; lokal, nicht \u00f6ffentlich) |
| Live-Migration: DB-Verzeichnis, .db_root-Marker, Backup-Verzeichnis | Venv venv_bot_20260802 (enth\u00e4lt "bot", nicht "bot6"; Rebuild unverh\u00e4ltnism\u00e4\u00dfig) |
| User-Agent bot6-local-rag \u2192 homebot-local-rag (Code + Doku) | LM Studio, MCP-Server, laufende Prozesse (nicht antasten) |
| Watcher-Mutex-Rename + Restart, pip-audit-Retry, Saberta-Commit-Rebase | |

## Definition of Done

| # | Kriterium | Pr\u00fcfmethode | Status |
|---|-----------|---------------|--------|
| 1 | git grep -i bot6 liefert nur noch Archive/Workdocs + 1 Hygiene-Beispiel | git grep | \u2610 |
| 2 | Volltestsuite gr\u00fcn (>= 1031 passed) | run_pytest_venv.ps1 | \u2610 |
| 3 | Release-Gate bei Commit gr\u00fcn (pytest + Lizenz + Hygiene) | pre-commit-Hook | \u2610 |
| 4 | App-Startpfad intakt: .db_root + DB-Ordner zeigen auf homebot_dbs | Inspektion + Resolver-Smoke | \u2610 |
| 5 | Backups intakt: homebot_backups vollst\u00e4ndig (2,7 GB / 66 Dateien) | Get-ChildItem | \u2610 |
| 6 | Watcher l\u00e4uft mit neuem Mutex, Autosave-Log aktuell | autosave.log | \u2610 |
| 7 | Commit d4e756a sagt "Sabetra", Remote = lokale HEAD | git log + ls-remote | \u2713 |
| 8 | pip-audit-Scan: Ergebnis dokumentiert (Advisories oder Netzwerk-Fehlschlag) | Scanner-Report | \u2610 |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Risiko | Entscheidung |
|---|--------|-----|--------|--------|--------------|
| A | Kompat-Fallback (HOMEBOT_* + BOT6_* lesen, doppelte Verzeichnisse) | bricht nichts | Dauerzustand mit zwei Namen = exakt das 2026-07/08-DB-Root-Dualit\u00e4tsproblem (AGENTS.md); Silent-Fallbacks verboten | mittel | \u274c |
| B | Vollst\u00e4ndiges Rename + einmalige Live-Migration, kein Fallback | ein Name, kein Dual-Root-Risiko, Root-Cause-Fix | erfordert koordinierten Umzug (App war gestoppt \u2014 verifiziert) | niedrig (Rollback = Rename zur\u00fcck) | \u2705 |
| C | Nur Prosa-Rename, Env-Vars/Pfade bleiben BOT6 | minimal | "offener Punkt" nicht erf\u00fcllt, Marke bleibt in Code/Tests/Doku | \u2014 | \u274c |

> **Auswahl:** B \u2014 keine BOT6_*-Env-Vars auf User-/Machine-Ebene gesetzt (verifiziert), App-Prozesse nicht aktiv (verifiziert) \u2192 saubere Migration ohne Fallback m\u00f6glich; passt zur AGENTS.md-Konvention "Root-Cause-Fixes, keine silent-fallbacks".

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|-------|
| 1 | 43 Dateien mit "bot6" (git grep); davon 8 Archive/Workdocs = Historie (bleiben) | git grep -i bot6, 2026-09-04 |
| 2 | 9 Env-Vars: DB_ROOT, USER_ID, TOKEN_SCALING_OVERRIDES, RG_BIN, AUTOSAVE, PYTHON, BACKUP_ROOT, BACKUP_LOG, VENV | git grep -n "BOT6_" |
| 3 | BOT6_* nirgends als User-/Machine-Env gesetzt | [Environment]::GetEnvironmentVariable |
| 4 | Produktiv-DB: ~\\.local\\share\\bot6_dbs; .db_root-Marker zeigt dorthin | .db_root, Get-ChildItem |
| 5 | ~/.cache/bot6 existiert NICHT \u2192 nichts zu migrieren | Test-Path |
| 6 | bot6_backups: 2,7 GB / 66 Dateien; App + Streamlit nicht aktiv; MCP-Server l\u00e4uft (nicht antasten) | Get-ChildItem / Win32_Process |
| 7 | Watcher aktiv, Mutex Global\\bot6-git-autosave-watcher | Win32_Process, autosave_watcher.ps1:49 |
| 8 | Hygiene-Gate verietet echte User-Pfade (z.B. C:\\Users\\bot6) in aktiven Dateien \u2192 Beispiel in check_release_hygiene.py:8 bewusst behalten | check_release_hygiene.py:45-48 |
| 9 | docs/README.md L104-124 = datierter Incident-Report (2026-08-10) \u2192 bot6-Namen dort = korrekte Historie (bleiben); Live-Mechanismen L88/L134/L138 werden aktualisiert | docs/README.md |

## Risiko & Impact-Matrix

| # | Risiko | W | A | Minderung | Status |
|---|--------|---|---|-----------|--------|
| 1 | DB-Ordner-Umzug w\u00e4hrend Watcher-Cycle \u2192 Backup-Sprung | niedrig | niedrig | App gestoppt; Backup-Fehler nicht destruktiv (kein Data-Loss) | offen |
| 2 | Doppelte Watcher-Instanz nach Mutex-Rename | niedrig | mittel | alten Watcher stoppen, neuen starten, Log verifizieren; Fallback: Mutex-Name behalten | offen |
| 3 | Encoding-Ver\u00e4nderung bei Massen-Ersetzung | niedrig | hoch | .NET IO Read/WriteAllText (UTF-8, BOM-Erkennung) + py_compile + git-diff-Review | offen |
| 4 | Half-Rename-State wird committet | niedrig | mittel | Pre-Commit-Gate (pytest) blockiert inkonsistente States | offen |
| 5 | Rebase trifft laufende Watcher-Commits (Lock) | niedrig | niedrig | Rebase am Ende; bei Lock-Warnung Retry | offen |

## \u00c4nderungen

| # | Datei | \u00c4nderung | Test-Ergebnis |
|---|-------|-----------|---------------|
| (nach Ausf\u00fchrung erg\u00e4nzen) | | | |

## Rollback-Strategie

| Schritt | Aktion | Befehl / Referenz |
|---------|--------|-------------------|
| 1 | Code-Rollback (vor Migration) | git checkout -- . (Stand vor Rename) |
| 2 | DB-Ordner zur\u00fcck | Rename-Item ~\\.local\\share\\homebot_dbs bot6_dbs |
| 3 | Marker zur\u00fcck | .db_root \u2190 ~\\.local\\share\\bot6_dbs |
| 4 | Backups zur\u00fcck | Rename-Item ~\\homebot_backups bot6_backups |
| 5 | Watcher neu starten (alter Mutex) | Start-Befehl aus autosave_watcher.ps1 |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| (nach Ausf\u00fchrung erg\u00e4nzen) | | | |
