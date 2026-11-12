@echo off
chcp 65001 >nul
cd /d D:\ttxt
if "%~1"=="" (
  echo 用法: ttxt.cmd ^<命令^>
  echo 常用: doctor / crawl / clean / analyze / write / model / serve / agents / selftest
  echo 打开面板请双击: scripts\start-dashboard.cmd
  pause
  exit /b 2
)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PIP_CACHE_DIR=D:\ttxt\.pip-cache
if exist "D:\ttxt\.venv\Scripts\python.exe" (
  "D:\ttxt\.venv\Scripts\python.exe" -X utf8 -m ttxt %*
) else (
  echo venv missing: run scripts\setup_env.ps1 first
  exit /b 1
)
