@echo off
setlocal
set "PY=d:\ttxt\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo ==================================================
echo  Trae model config fix
echo  Quit Trae completely BEFORE running this.
echo ==================================================
echo.
"%PY%" "D:\ttxt\_model_fix\apply_fix.py"
echo.
echo Exit code: %ERRORLEVEL%
echo.
pause
