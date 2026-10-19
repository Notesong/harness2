@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Preparing Harness2 for the first time...
    py -3.12 -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "from importlib.metadata import distribution; import openpyxl, pptx; distribution('harness2')" >nul 2>&1
if errorlevel 1 (
    echo Installing Harness2 and document readers...
    ".venv\Scripts\python.exe" -m pip install -e ".[render]"
    if errorlevel 1 goto failed
)
start "" ".venv\Scripts\pythonw.exe" -m harness2.ui.server
exit /b 0
:failed
echo.
echo Setup could not finish. Install Python 3.12 and try again.
pause
exit /b 1
