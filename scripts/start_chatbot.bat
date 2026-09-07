@echo off
echo 🚀 STARTE MISTRAL CHATBOT MIT RTX 4090 OPTIMIERUNG...
echo 🎮 Hardware: RTX 4090 + Ryzen 7 5800X + 64GB RAM
echo =====================================================
echo.

cd /d "%~dp0.."

echo 📂 Wechsle zu Projektverzeichnis: %CD%
echo.

REM Aktiviere Virtual Environment
call "venv_mistral_gguf\Scripts\activate.bat"

REM Starte optimierten Chatbot
python start_chatbot_original.py

REM Bei Fehler Fenster offen lassen
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ❌ Es ist ein Fehler aufgetreten!
    pause
)
set RAG_MIN_SCORE=0.0
set RAG_MULTIQUERY=1
set RAG_MQ_N=6
set RAG_MQ_K=6
REM Optional presentation
set CITATION_INLINE_DETAILS=1
set APPEND_SOURCES_BLOCK=1

echo 🐍 Verwende Python: %PYTHON_EXE%
echo.

echo ⏳ Starte GUI... (Das kann einen Moment dauern)
echo 💡 Modell muss beim ersten Start geladen werden!
echo 📝 Schau in die GUI für Progress-Updates.
echo.

"%PYTHON_EXE%" gui.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo ❌ GUI ist mit Fehlercode %ERRORLEVEL% beendet
    echo 🩺 Führe Debug-Check aus...
    "%PYTHON_EXE%" debug_chatbot.py
)

echo.
echo ✅ Programm beendet.
pause
