# BOT STARTUP SCRIPT - Mit korrektem Embedding-Modell
# Verwendung: .\start_bot_fixed.ps1

# Portabel: Repo-Root = Ordner dieses Skripts
$RepoRoot = $PSScriptRoot

Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "STARTE BOT MIT KORRIGIERTEM EMBEDDING-MODELL" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""

# 1. Stoppe alte Streamlit-Prozesse
#    (bewusst NUR streamlit: fremde Python-Prozesse — z. B. LM Studio/llama-server —
#     werden nie angefasst)
Write-Host "Stoppe alte Streamlit-Prozesse..." -ForegroundColor Yellow
Get-Process | Where-Object {$_.ProcessName -like "*streamlit*"} | ForEach-Object {
    try {
        Write-Host "   Stoppe: $($_.ProcessName) (PID: $($_.Id))" -ForegroundColor Gray
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    } catch {}
}
Start-Sleep -Seconds 1
Write-Host "Alte Streamlit-Prozesse gestoppt`n" -ForegroundColor Green

# 2. Aktiviere Virtual Environment (portabel: venv_bot_20260802 (Produktiv) > .venv > venv > venv_mistral_gguf)
Write-Host "Aktiviere Virtual Environment..." -ForegroundColor Yellow
$activate = $null
foreach ($candidate in @('venv_bot_20260802', '.venv', 'venv', 'venv_mistral_gguf')) {
    $path = Join-Path $RepoRoot (Join-Path $candidate 'Scripts\Activate.ps1')
    if (Test-Path $path) { $activate = $path; break }
}
if (-not $activate) {
    throw "Kein Projekt-venv unter $RepoRoot gefunden (.venv, venv, venv_bot_20260802 oder venv_mistral_gguf erwartet)"
}
& $activate
$venvPython = Join-Path (Split-Path (Split-Path $activate -Parent) -Parent) 'Scripts\python.exe'
Write-Host "Virtual Environment aktiviert ($activate)`n" -ForegroundColor Green

# 2b. Bereinige potenziell kollidierende Env-Variablen (Conda/Python)
# Verhindert Misch-Kontext zwischen unterschiedlichen Python-Umgebungen.
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:CONDA_PREFIX -ErrorAction SilentlyContinue
Remove-Item Env:CONDA_DEFAULT_ENV -ErrorAction SilentlyContinue

if (-not (Test-Path $venvPython)) {
    throw "Python executable im venv nicht gefunden: $venvPython"
}

# 3. Setze Environment-Variable
Write-Host "Setze Embedding-Modell..." -ForegroundColor Yellow
$env:RAG_EMBEDDING_MODEL = "intfloat/multilingual-e5-large"
Write-Host "RAG_EMBEDDING_MODEL = $env:RAG_EMBEDDING_MODEL`n" -ForegroundColor Green

# 4. Zeige Konfiguration
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "KONFIGURATION:" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "   Workspace: $RepoRoot" -ForegroundColor White
Write-Host "   VEnv: $((Split-Path (Split-Path $activate -Parent) -Leaf))" -ForegroundColor White
Write-Host "   Python: $venvPython" -ForegroundColor White
Write-Host "   Script: enhanced_streamlit_bot.py" -ForegroundColor White
Write-Host "   Embedding-Modell: $env:RAG_EMBEDDING_MODEL" -ForegroundColor White
Write-Host "   Port: 8501" -ForegroundColor White
Write-Host ""

# 5. Starte Bot
Write-Host "Starte Bot..." -ForegroundColor Yellow
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""

# Wechsle zum Workspace
Set-Location $RepoRoot

# Starte Streamlit explizit über das venv-Python
& $venvPython -m streamlit run (Join-Path $RepoRoot 'enhanced_streamlit_bot.py')

# Falls der Bot stoppt, zeige Meldung
Write-Host ""
Write-Host "=" * 80 -ForegroundColor Red
Write-Host "BOT WURDE BEENDET" -ForegroundColor Red
Write-Host "=" * 80 -ForegroundColor Red
