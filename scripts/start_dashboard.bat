@echo off
REM Performance Dashboard Launcher
REM Starts the Streamlit dashboard for RAG system monitoring

echo Starting RAG Performance Dashboard...
echo.

REM Check if virtual environment exists
if exist "venv_mistral_gguf\Scripts\activate.bat" (
    echo Activating virtual environment...
    call venv_mistral_gguf\Scripts\activate.bat
) else (
    echo Warning: Virtual environment not found at venv_mistral_gguf
    echo Attempting to run with system Python...
)

REM Check if Streamlit is installed
python -c "import streamlit" 2>nul
if errorlevel 1 (
    echo.
    echo Streamlit not found. Installing required packages...
    pip install streamlit plotly pandas
    if errorlevel 1 (
        echo Failed to install required packages.
        pause
        exit /b 1
    )
)

REM Start the dashboard
echo.
echo Starting dashboard at http://localhost:8501
echo Press Ctrl+C to stop the dashboard
echo.

streamlit run performance_dashboard.py --server.port 8501 --server.address localhost

if errorlevel 1 (
    echo.
    echo Dashboard failed to start. Check the error messages above.
    pause
)
