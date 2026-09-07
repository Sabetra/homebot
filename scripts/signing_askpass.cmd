@echo off
rem Signing-Askpass-Wrapper: wird von ssh-keygen/ssh-add ueber SSH_ASKPASS aufgerufen.
rem Liefert die Signing-Passphrase (DPAPI-Store) als eine Zeile auf stdout.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0signing_askpass.ps1"
exit /b %ERRORLEVEL%