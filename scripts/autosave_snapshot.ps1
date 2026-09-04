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
