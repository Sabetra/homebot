# PowerShell runner: führt die deterministischen Tests im Projekt-venv aus
# (vermeidet systemische Python-Quellkodierungs-Fehler).
#
# Portabel: venv-Auflösung relativ zum Repo-Root (Elternordner dieses Skripts).
# Reihenfolge identisch zum Pre-Commit-Hook (.githooks/pre-commit):
#   $env:BOT6_VENV > venv_bot_20260802 (Produktiv) > .venv > venv > venv_mistral_gguf
# Pass 1: erste Venv, die pytest importieren kann (Gate-Voraussetzung),
# Pass 2: erste vorhandene Venv (Fallback).
# So pickt das Skript keine unausgebaute Venv (z. B. .venv ohne aiosqlite/plotly).

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot

$venvCandidates = @(
    (Join-Path $RepoRoot 'venv_bot_20260802\Scripts\Activate.ps1'),
    (Join-Path $RepoRoot '.venv\Scripts\Activate.ps1'),
    (Join-Path $RepoRoot 'venv\Scripts\Activate.ps1'),
    (Join-Path $RepoRoot 'venv_mistral_gguf\Scripts\Activate.ps1')
)
if ($env:BOT6_VENV) {
    $venvCandidates = @((Join-Path $env:BOT6_VENV 'Scripts\Activate.ps1')) + $venvCandidates
}

# Pass 1: erste Venv, die pytest importieren kann (Gate-Voraussetzung).
$venvPath = $null
foreach ($c in $venvCandidates) {
    if (-not (Test-Path $c)) { continue }
    $py = Join-Path (Split-Path $c) 'python.exe'
    if (Test-Path $py) {
        & $py -c "import pytest" *> $null
        if ($LASTEXITCODE -eq 0) { $venvPath = $c; break }
    }
}
# Pass 2: Fallback auf die erste vorhandene Venv (Fehler wird dann sichtbar).
if (-not $venvPath) {
    foreach ($c in $venvCandidates) {
        if (Test-Path $c) { $venvPath = $c; break }
    }
}

if (-not $venvPath) {
    throw "No project venv activation script found. Expected one of: $($venvCandidates -join ', ')"
}

Write-Host "run_pytest_venv: verwende venv: $venvPath"
& $venvPath
Set-Location $RepoRoot

# $args avoids PowerShell parameter-prefix collisions with pytest flags like -p
if ($args.Count -eq 0) {
    python -m pytest tests/ -q --no-header -p no:cacheprovider
}
else {
    python -m pytest @args
}

exit $LASTEXITCODE
