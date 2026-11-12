@echo off
rem Commit (optional, -m) and push current branch. Token comes from
rem .secrets\github_token (saved once via scripts\login.cmd) or GITHUB_TOKEN.
setlocal
"%~dp0..\.venv\Scripts\python.exe" -X utf8 "%~dp0git_push.py" %*
exit /b %ERRORLEVEL%
