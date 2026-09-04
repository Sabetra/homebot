<#
.SYNOPSIS
    Dauerlaeufer, der autosave_snapshot.ps1 alle 10 Minuten aufruft.

.BESCHREIBUNG
    Ersatz fuer die Windows-Aufgabenplanung: Auf diesem Rechner meldet der
    Aufgabenplaner zwar Erfolg (LastTaskResult 0), startet die Aktion aber
    nachweislich nicht - der unbedingte Startmarker des Wrappers erschien in
    keinem einzigen Lauf. Dieser Watcher umgeht das vollstaendig.

    Start erfolgt automatisch bei der Anmeldung ueber eine Verknuepfung im
    Autostart-Ordner (shell:startup).

    Manuell starten:
        powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\scripts\autosave_watcher.ps1

    Laeuft er?  ->  Get-Content <repo>\monitoring\autosave.log -Tail 5
    Beenden     ->  Get-Process powershell | Where-Object { $_.Path -and $_.StartTime } | Stop-Process
                    (oder schlicht abmelden)
#>

$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent $PSScriptRoot

$IntervalSeconds = 600
$SnapshotScript  = Join-Path $PSScriptRoot 'autosave_snapshot.ps1'
$LogFile         = Join-Path $RepoRoot 'monitoring\autosave.log'

# Datenbank-Backup: eigenes Skript, eigenes Log, eigenes Selbst-Gate.
# Es beendet sich in Sekundenbruchteilen, wenn das heutige Backup schon
# existiert - deshalb ist der Aufruf in jedem Zyklus unbedenklich.
$DbBackupScript = Join-Path $PSScriptRoot 'db_backup.py'
# venv-Python portabel aufloesen (Reihenfolge: venv_bot_20260802 (Produktiv) > .venv > venv > venv_mistral_gguf)
$DbBackupPython = $null
foreach ($candidate in @('venv_bot_20260802', '.venv', 'venv', 'venv_mistral_gguf')) {
    $exe = Join-Path $RepoRoot (Join-Path $candidate 'Scripts\python.exe')
    if (Test-Path $exe) { $DbBackupPython = $exe; break }
}

function Write-WatcherLog {
    param([string]$Message)
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    try { Add-Content -Path $LogFile -Value $line -Encoding utf8 } catch { }
}

# Einmal-Sperre: verhindert, dass sich Watcher stapeln, wenn das Skript
# zusaetzlich zum Autostart noch von Hand gestartet wird.
$mutex = New-Object System.Threading.Mutex($false, 'Global\homebot-git-autosave-watcher')
if (-not $mutex.WaitOne(0, $false)) {
    Write-WatcherLog "watcher: bereits aktiv, dieser Start (PID $PID) wird beendet"
    exit 0
}

Write-WatcherLog "watcher gestartet (PID $PID, Intervall ${IntervalSeconds}s)"

function Invoke-ChildProcess {
    # Kindprozess mit hartem Timeout starten (Befund 2026-08: Watcher inaktiv
    # seit 10.08. — ein abhaengiges Kind koennte die Kette stumm lahmlegen).
    # Gibt den Exitcode zurueck; 124 = durch Timeout erzwungen beendet.
    param(
        [string]$ChildFilePath,
        [string]$ChildArgumentList,
        [int]$TimeoutSeconds
    )
    try {
        $proc = Start-Process -FilePath $ChildFilePath -ArgumentList $ChildArgumentList -PassThru -WindowStyle Hidden
        if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
            $proc.Kill()
            Write-WatcherLog "watcher: KIND-TIMEOUT nach ${TimeoutSeconds}s erzwungen beendet ($ChildFilePath)"
            return 124
        }
        return $proc.ExitCode
    }
    catch {
        Write-WatcherLog "watcher: Kindstart fehlgeschlagen - $($_.Exception.Message)"
        return 1
    }
}

while ($true) {
    $cycleStart = Get-Date

    # Git-Snapshot (Timeout 15 min: selbst groesse WIP-Saetze bleiben darunter)
    $snapCode = Invoke-ChildProcess -ChildFilePath 'powershell.exe' `
        -ChildArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$SnapshotScript`"" `
        -TimeoutSeconds 900

    # Code-Snapshot und Daten-Backup sind bewusst entkoppelt: Ein Fehler beim
    # DB-Backup darf den Git-Snapshot nie verhindern (und umgekehrt).
    $dbCode = 0
    if ($DbBackupPython -and (Test-Path $DbBackupPython)) {
        $dbCode = Invoke-ChildProcess -ChildFilePath $DbBackupPython `
            -ChildArgumentList "`"$DbBackupScript`"" -TimeoutSeconds 1800
    }
    else {
        # Fallback: python aus der PATH
        $pyCmd = Get-Command python -ErrorAction SilentlyContinue
        if ($pyCmd) {
            $dbCode = Invoke-ChildProcess -ChildFilePath $pyCmd.Source `
                -ChildArgumentList "`"$DbBackupScript`"" -TimeoutSeconds 1800
        }
        else {
            Write-WatcherLog "watcher: Kein Python fuer DB-Backup gefunden"
            $dbCode = 1
        }
    }
    if ($dbCode -ne 0) {
        Write-WatcherLog "watcher: DB-Backup meldete Exitcode $dbCode (Details: monitoring\db_backup.log)"
    }

    # Heartbeat: jeder Zyklus ist jetzt im Log nachvollziehbar.
    $elapsedSeconds = [int]((Get-Date) - $cycleStart).TotalSeconds
    if ($snapCode -eq 0 -and $dbCode -eq 0) {
        Write-WatcherLog "watcher: Zyklus ok (${elapsedSeconds}s)"
    }
    else {
        Write-WatcherLog "watcher: Zyklus mit Fehlern (${elapsedSeconds}s, snapshot=$snapCode, db=$dbCode)"
    }

    Start-Sleep -Seconds $IntervalSeconds
}
