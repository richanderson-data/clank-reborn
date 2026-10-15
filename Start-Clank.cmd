@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: open a terminal in this folder and run:
  echo   py -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -e ".[autonomy]"
  echo Then start Ollama and open Start-Clank.cmd again.
  pause
  exit /b 1
)
set "CLANK_PROJECT=%~dp0."
if not "%~1"=="" set "CLANK_PROJECT=%~1"
".venv\Scripts\python.exe" -m clank --web --port 0 --autonomous --allow-dir "%CLANK_PROJECT%"
if errorlevel 1 pause
