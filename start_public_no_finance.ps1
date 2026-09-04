# PUBLIC BOT STARTUP (Finance Tab disabled)
# Usage: .\start_public_no_finance.ps1
#
# Portabel: arbeitet aus dem eigenen Verzeichnis (Repo-Root).
# venv-Auflösung: $env:BOT6_VENV > venv_bot_20260802 (Produktiv) > .venv > venv > venv_mistral_gguf > System-Python

$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }

Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "STARTE PUBLIC BOT (OHNE FINANCE TAB)" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""

Write-Host "Stoppe alte Python/Streamlit-Prozesse..." -ForegroundColor Yellow
Get-Process | Where-Object { $_.ProcessName -like "*python*" -or $_.ProcessName -like "*streamlit*" } | ForEach-Object {
    try {
        Write-Host "  Stoppe: $($_.ProcessName) (PID: $($_.Id))" -ForegroundColor Gray
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    } catch {}
}
Write-Host ""

# ── venv-Auflösung (portabel) ─────────────────────────────────────────────
# Reihenfolge: Produktiv-venv zuerst (AGENTS.md: IMMER venv_bot_20260802),
# Legacy-Venvs (.venv, venv) nur als Fallback — sie sind unbefriedigt
# (fehlen u. a. plotly/aiosqlite). Konvention: siehe run_pytest_venv.ps1.
$venvCandidates = @(
    "$RepoRoot\venv_bot_20260802",
    "$RepoRoot\.venv",
    "$RepoRoot\venv",
    "$RepoRoot\venv_mistral_gguf"
)
$venvDir = $env:BOT6_VENV
if (-not $venvDir) {
    $venvDir = $venvCandidates | Where-Object { Test-Path (Join-Path $_ "Scripts\python.exe") } | Select-Object -First 1
}

if ($venvDir) {
    & (Join-Path $venvDir "Scripts\Activate.ps1")
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "Python executable im venv nicht gefunden: $venvPython"
    }
} else {
    Write-Host "Kein Projekt-venv gefunden - nutze System-Python." -ForegroundColor Yellow
    $venvPython = (Get-Command python -ErrorAction Stop).Source
}

Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:CONDA_PREFIX -ErrorAction SilentlyContinue
Remove-Item Env:CONDA_DEFAULT_ENV -ErrorAction SilentlyContinue

$env:RAG_EMBEDDING_MODEL = "intfloat/multilingual-e5-large"
$env:APP_ENABLE_FINANCE_TAB = "0"

$entryScript = Join-Path $RepoRoot "enhanced_streamlit_bot.py"

Write-Host "Konfiguration:" -ForegroundColor Cyan
Write-Host "  Python: $venvPython" -ForegroundColor White
Write-Host "  Script: $entryScript" -ForegroundColor White
Write-Host "  RAG_EMBEDDING_MODEL: $env:RAG_EMBEDDING_MODEL" -ForegroundColor White
Write-Host "  APP_ENABLE_FINANCE_TAB: $env:APP_ENABLE_FINANCE_TAB (PUBLIC)" -ForegroundColor White
Write-Host ""

Set-Location $RepoRoot
& $venvPython -m streamlit run $entryScript
