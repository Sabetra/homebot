<#
.SYNOPSIS
    Laedt den Homebot-Signing-Key (ED25519) idempotent in den lokalen ssh-agent.

.DESCRIPTION
    Fail-closed und idempotent:
      - Key bereits im Agent -> sofort Exit 0 (schneller Pfad, ~0.2 s)
      - sonst: Passphrase aus dem Windows-DPAPI-Store lesen (CurrentUser,
        verschluesselt, nie Klartext auf Disk) und ssh-add ueber ein
        transientes Askpass ausloesen. Die Askpass-Datei enthaelt KEIN
        Passwort; der Wert kommt ausschliesslich aus der Prozessumgebung
        (SG_PASSPHRASE) und wird nach Abschluss zusammen mit der Datei
        entfernt.

    Exitcodes: 0 = Key im Agent, 1 = Fehler (fail-closed).

    ASCII-only aus PS-5.1-Kompatibilitaet (Encoding der .ps1 ohne BOM).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\load_signing_key.ps1

.NOTES
    DPAPI-Store erzeugen (einmalig, z. B. nach Passphrase-Wechsel):
        $b = [Text.Encoding]::Unicode.GetBytes('<Passphrase>')
        $d = [Security.Cryptography.ProtectedData]::Protect($b, $null,
              [Security.Cryptography.DataProtectionScope]::CurrentUser)
        [IO.File]::WriteAllBytes('<%LOCALAPPDATA%\homebot\signing_passphrase.txt>', $d)
#>
[CmdletBinding()]
param(
    [string]$KeyPath   = (Join-Path $env:USERPROFILE '.ssh\github_signing_ed25519'),
    [string]$StoreFile = (Join-Path $env:LOCALAPPDATA 'homebot\signing_passphrase.txt')
)
# Bewusst NICHT 'Stop': ssh-add/ssh-keygen schreiben normale Meldungen auf
# stderr; PS 5.1 macht daraus mit 'Stop' + 2>&1 terminierende Exceptions
# (gleiche Falle wie im autosave-Skript). Fehlerbehandlung erfolgt ueber
# $LASTEXITCODE-Pruefungen und explizite throw-Anweisungen (fail-closed).
$ErrorActionPreference = 'Continue'

# [System.Security.Cryptography.ProtectedData] liegt in System.Security.dll;
# PS loest den Typ nur auf, wenn die Assembly geladen ist. Add-Type ist
# idempotent (null Kosten, falls sie schon geladen ist).
Add-Type -AssemblyName System.Security

# SSH-Binaries PINNEN: System-OpenSSH (C:\Windows\System32\OpenSSH).
# Erfahrungswert dieses Setups (2026-09-07): Git-for-Windows-Builds
# (C:\Program Files\Git\usr\bin) erhalten am Agent-Pipe 'Device or resource
# busy' (MSYS-Pipe-Handling), waehrend der System-Client zuverlaessig
# verbindet (inkl. Default-Pipe-Fallback). Deshalb keine PATH-Aufloesung.
$SshAdd    = 'C:\Windows\System32\OpenSSH\ssh-add.exe'
$SshKeygen = 'C:\Windows\System32\OpenSSH\ssh-keygen.exe'
if (-not ((Test-Path $SshAdd) -and (Test-Path $SshKeygen))) {
    throw "System-OpenSSH nicht gefunden (C:\Windows\System32\OpenSSH). Windows-Feature 'OpenSSH Client' erforderlich."
}

function Read-DpapiPassphrase([string]$File) {
    if (-not (Test-Path $File)) { throw "DPAPI-Store nicht gefunden: $File" }
    $raw = [System.IO.File]::ReadAllBytes($File)
    $plainBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $raw, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    return [System.Text.Encoding]::Unicode.GetString($plainBytes)
}

# 1) Basis-Pruefungen (fail-closed)
if (-not (Test-Path $KeyPath))       { throw "Signing-Key nicht gefunden: $KeyPath" }
if (-not (Test-Path "$KeyPath.pub")) { throw "Oeffentlicher Key fehlt: $KeyPath.pub" }

# 2) Fingerprint aus dem oeffentlichen Key (keine Passphrase noetig)
$fpLine = ((& $SshKeygen -lf "$KeyPath.pub" 2>$null) -join ' ')
$fp = @($fpLine -split '\s+')[1]
if (-not $fp) { throw "Fingerprint konnte nicht bestimmt werden (ssh-keygen fehlgeschlagen)." }

# 3) Idempotenz: Key bereits im Agent?
if (& $SshAdd -l 2>$null | Select-String -SimpleMatch $fp) {
    Write-Output "load_signing_key: OK - $fp ist bereits im ssh-agent."
    exit 0
}

# 4) Passphrase aus dem DPAPI-Store
$passphrase = Read-DpapiPassphrase $StoreFile
if (-not $passphrase) { throw "DPAPI-Store ist leer oder defekt." }

# 5) Transientes Askpass (Datei ohne Passwort; Wert aus der Prozessumgebung)
$bat = Join-Path $env:TEMP "sg_askpass_$PID.bat"
Set-Content -Path $bat -Value @('@echo off', 'echo %SG_PASSPHRASE%')

try {
    $env:SSH_ASKPASS         = $bat
    $env:SSH_ASKPASS_REQUIRE = 'force'
    $env:SG_PASSPHRASE       = $passphrase
    $out = @(& $SshAdd $KeyPath 2>&1)
    $addExit = $LASTEXITCODE
    foreach ($line in $out) { Write-Output ("ssh-add: " + $line.ToString()) }
    if ($addExit -ne 0) { throw "ssh-add fehlgeschlagen (Exit $addExit)." }
}
finally {
    Remove-Item -Path $bat -Force -ErrorAction SilentlyContinue
    Remove-Item Env:SG_PASSPHRASE -ErrorAction SilentlyContinue
    Remove-Item Env:SSH_ASKPASS -ErrorAction SilentlyContinue
    Remove-Item Env:SSH_ASKPASS_REQUIRE -ErrorAction SilentlyContinue
}

# 6) Verifikation (fail-closed)
if (& $SshAdd -l 2>$null | Select-String -SimpleMatch $fp) {
    Write-Output "load_signing_key: OK - $fp im ssh-agent."
    exit 0
}
throw "load_signing_key: FEHLER - Key liegt nach ssh-add nicht im Agent."