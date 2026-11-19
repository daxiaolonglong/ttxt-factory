@echo off
setlocal
set "PY=d:\ttxt\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "TRAE=%LOCALAPPDATA%\Programs\Trae CN\Trae CN.exe"
echo ==================================================
echo   Fix Trae model config and restart Trae
echo   1) close Trae  2) patch config  3) open Trae
echo ==================================================
echo.
echo [1/3] Closing Trae ...
taskkill /F /IM "Trae CN.exe" /T >nul 2>&1
timeout /t 4 /nobreak >nul
echo.
echo [2/3] Patching config ...
"%PY%" "D:\ttxt\_model_fix\apply_fix.py"
set RC=%ERRORLEVEL%
echo.
echo [3/3] Starting Trae ...
start "" "%TRAE%"
timeout /t 3 /nobreak >nul
echo.
echo Patch exit code: %RC%   (0 = success)
echo.
pause
