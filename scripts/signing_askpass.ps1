#requires -Version 5.1
<#
Signing-Askpass: liefert die Signing-Passphrase aus dem DPAPI-Store (CurrentUser-Scope).

Wird von ssh-keygen / ssh-add ueber SSH_ASKPASS aufgerufen (empfohlen: SSH_ASKPASS_REQUIRE=force).
Vertrag: exakt die Passphrase als eine Zeile auf stdout.
Bei Fehlern: KEIN stdout, Exit 1 (kein Hang, kein Logging, fail-closed).

DPAPI-Store: %LOCALAPPDATA%\homebot\signing_passphrase.txt
  (CurrentUser-ProtectedData; nur der selbe Windows-User kann entsperren)
#>
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Security
    $store = Join-Path $env:LOCALAPPDATA 'homebot\signing_passphrase.txt'
    if (-not (Test-Path $store)) { exit 1 }
    $raw = [System.IO.File]::ReadAllBytes($store)
    $plain = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $raw,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    [Console]::WriteLine([System.Text.Encoding]::Unicode.GetString($plain))
    exit 0
}
catch {
    exit 1
}