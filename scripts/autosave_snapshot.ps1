<#
.SYNOPSIS
    Deterministisches Sicherheitsnetz: committet den Arbeitsstand automatisch.

.BESCHREIBUNG
    Laeuft per Windows-Aufgabenplanung im Minutentakt und legt einen
    Autosave-Commit an, wenn sich etwas geaendert hat. Bewusst unabhaengig
    vom lokalen LLM: der Agent kann diesen Schutz nicht vergessen,
    ueberspringen oder falsch benennen.

    Wiederherstellung einer einzelnen Datei:
        git -C <repo-root> log --oneline -- pfad/zur/datei.py
        git -C <repo-root> checkout <sha> -- pfad/zur/datei.py

    Letzten sauberen Stand einer Datei suchen:
        git -C <repo-root> log -p --follow -- pfad/zur/datei.py

.HINWEIS
    Setzt eine korrekte .gitignore voraus. Ohne die Eintraege im Block
    "Autosave-Absicherung" wuerden venvs (6-8 GB) mit eingecheckt.
#>

# Bewusst NICHT 'Stop': git schreibt Hinweise (z. B. "LF will be replaced by
# CRLF") auf stderr. PowerShell 5.1 verwandelt native stderr-Zeilen in
# ErrorRecords; mit 'Stop' bricht das Skript dadurch ab, bevor der Commit
# laeuft - im Aufgabenplaner-Kontext sogar ohne verwertbare Meldung.
# Fehlerbehandlung erfolgt stattdessen ueber explizite $LASTEXITCODE-Pruefungen.
$ErrorActionPreference = 'Continue'

# Portabel: Repo-Root = Elternordner dieses Skripts (scripts/).
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogFile  = Join-Path $RepoRoot 'monitoring\autosave.log'

function Write-AutosaveLog {
    param([string]$Message)
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

try {
    if (-not (Test-Path (Join-Path $RepoRoot '.git'))) {
        throw "Kein Git-Repository unter $RepoRoot"
    }

    $logDir = Split-Path $LogFile -Parent
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    # Laufende Rebase-/Merge-Operation nicht stoeren.
    $gitDir = Join-Path $RepoRoot '.git'
    foreach ($marker in @('MERGE_HEAD', 'REBASE_HEAD', 'CHERRY_PICK_HEAD')) {
        if (Test-Path (Join-Path $gitDir $marker)) {
            Write-AutosaveLog "uebersprungen: laufende Git-Operation ($marker)"
            exit 0
        }
    }

    git -C $RepoRoot add -A
    if ($LASTEXITCODE -ne 0) {
        throw "git add fehlgeschlagen (Exitcode $LASTEXITCODE)"
    }

    # Exitcode 0 = nichts vorgemerkt, 1 = Aenderungen vorhanden.
    git -C $RepoRoot diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        exit 0
    }

    # --- L2 Secret/Key-Guard (2026-09-07, fail-closed) -----------------------
    # Der Autosave ueberspringt das pre-commit-Gate (HOMEBOT_AUTOSAVE=1) und
    # muss sich daher selbst absichern. Verhalten:
    #   exit 0: alles sauber -> weiter
    #   exit 1: geflaggte Pfade (stdout) werden UNSTAGED (Dateien bleiben in
    #           der Arbeitsbaeume), der Rest wird committet. Jeder Durchlauf
    #           meldet neu -> dauerhafte Erinnerung, bis der Mensch es loest.
    #   exit 2: Guard-Fehler -> fail-closed, kein Commit (ungescannter
    #           Content darf keine Historie erreichen).
    #   kein Python / kein Guard-Skript: ebenfalls fail-closed (kein Commit).
    $guardScript = Join-Path $RepoRoot 'scripts\secret_guard.py'
    if (-not (Test-Path $guardScript)) {
        Write-AutosaveLog "FEHLER: scripts\secret_guard.py fehlt (fail-closed, kein Commit)"
        exit 1
    }
    $guardPy = $env:HOMEBOT_PYTHON
    if (-not $guardPy -or -not (Test-Path $guardPy)) { $guardPy = $null }
    if (-not $guardPy) {
        foreach ($cand in @(
                (Join-Path $RepoRoot 'venv_bot_20260802\Scripts\python.exe'),
                (Join-Path $RepoRoot '.venv\Scripts\python.exe'),
                (Join-Path $RepoRoot 'venv\Scripts\python.exe'),
                (Join-Path $RepoRoot 'venv_mistral_gguf\Scripts\python.exe'))) {
            if (Test-Path $cand) { $guardPy = $cand; break }
        }
    }
    if (-not $guardPy -or -not (Test-Path $guardPy)) {
        Write-AutosaveLog "FEHLER: Secret-Guard: kein Python-Interpreter (fail-closed, kein Commit)"
        exit 1
    }

    $guardLines = @(& $guardPy $guardScript '--staged' 2>&1)
    $guardExit  = $LASTEXITCODE
    $flagged    = @($guardLines | Where-Object { $_ -is [string] })
    $guardErr   = @($guardLines | Where-Object {
                        $_ -is [System.Management.Automation.ErrorRecord] } |
                   ForEach-Object { $_.ToString() })

    if ($guardExit -ge 2) {
        Write-AutosaveLog ("FEHLER: Secret-Guard: interner Fehler (fail-closed, kein Commit): {0}" -f ($guardErr -join ' | '))
        exit 1
    }
    if ($guardExit -eq 1) {
        foreach ($p in $flagged) {
            git -C $RepoRoot restore --staged -- $p
            Write-AutosaveLog ("Secret-Guard: ungestaged (bleibt in der Arbeitsbaeume): {0}" -f $p)
        }
        # Ist nach dem Unstage noch etwas zu committen?
        git -C $RepoRoot diff --cached --quiet
        if ($LASTEXITCODE -eq 0) {
            Write-AutosaveLog "Secret-Guard: alle Aenderungen geflaggt -> kein Commit in diesem Durchlauf"
            exit 0
        }
    }

    $stamp   = Get-Date -Format 'yyyy-MM-dd HH:mm'
    $changed = (git -C $RepoRoot diff --cached --name-only | Measure-Object -Line).Lines

    # Signal an .githooks/pre-commit: Das Sicherheitsnetz darf nie durch ein
    # rotes Gate blockiert werden - gerade kaputte Zwischenstaende muessen
    # gesichert werden. Bewusste Commits durchlaufen das Gate weiterhin.
    $env:HOMEBOT_AUTOSAVE = '1'
    try {
        git -C $RepoRoot commit -q -m "autosave $stamp ($changed Dateien)"
    }
    finally {
        Remove-Item Env:HOMEBOT_AUTOSAVE -ErrorAction SilentlyContinue
    }
    if ($LASTEXITCODE -ne 0) {
        throw "git commit fehlgeschlagen (Exitcode $LASTEXITCODE)"
    }

    Write-AutosaveLog "autosave: $changed Dateien committet"
    exit 0
}
catch {
    try { Write-AutosaveLog "FEHLER: $($_.Exception.Message)" } catch { }
    exit 1
}
