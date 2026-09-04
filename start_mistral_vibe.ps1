# MISTRAL-VIBE STARTUP SCRIPT
# Usage: .\start_mistral_vibe.ps1 [vibe-args...]

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$VibeArgs
)

$separator = ('=' * 80)
$venvRoot = Join-Path $PSScriptRoot 'venv_mistral_vibe'
$activateScript = Join-Path $venvRoot 'Scripts\Activate.ps1'

Write-Host $separator -ForegroundColor Cyan
Write-Host 'MISTRAL-VIBE START' -ForegroundColor Cyan
Write-Host $separator -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $activateScript)) {
    throw "Virtual environment not found: $activateScript"
}

Write-Host 'Activating separate virtual environment...' -ForegroundColor Yellow
& $activateScript

Set-Location $PSScriptRoot

Write-Host 'VEnv: venv_mistral_vibe' -ForegroundColor Green
Write-Host ("Command: vibe {0}" -f ($VibeArgs -join ' ')) -ForegroundColor Green
Write-Host ''

& vibe @VibeArgs

Write-Host ''
Write-Host $separator -ForegroundColor Red
Write-Host 'mistral-vibe finished' -ForegroundColor Red
Write-Host $separator -ForegroundColor Red