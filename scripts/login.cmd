@echo off
rem Save a GitHub PAT once; later pushes read it automatically.
setlocal
"%~dp0..\.venv\Scripts\python.exe" -X utf8 "%~dp0github_login.py" %*
exit /b %ERRORLEVEL%
