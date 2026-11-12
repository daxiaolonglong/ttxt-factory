@echo off
rem Push current branch to GitHub with a PAT from GITHUB_TOKEN.
setlocal
if "%GITHUB_TOKEN%"=="" (
  echo [ERROR] Set GITHUB_TOKEN first:  set GITHUB_TOKEN=ghp_xxx
  exit /b 1
)
"%~dp0..\.venv\Scripts\python.exe" -X utf8 "%~dp0git_push.py" %*
exit /b %ERRORLEVEL%
