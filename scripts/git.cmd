@echo off
rem Portable git wrapper (no system PATH pollution).
rem Usage: scripts\git.cmd status   /   scripts\git.cmd log --oneline -5
setlocal
set "GIT_EXE=%~dp0..\tools\git\cmd\git.exe"
if not exist "%GIT_EXE%" (
  echo [ERROR] portable git not found: %GIT_EXE%
  exit /b 1
)
"%GIT_EXE%" %*
exit /b %ERRORLEVEL%
