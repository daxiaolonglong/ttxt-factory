@echo off
chcp 65001 >nul
cd /d D:\ttxt
echo 托管状态(pid、子进程、重启次数、退出码都在 00_control\supervisor_state.json):
".venv\Scripts\python.exe" -X utf8 -m ttxt supervisor --status
echo.
echo 完整体检里请看这两行:
echo   [PASS/FAIL] ops.supervisor          —— 有没有人在托管(停摆当场可见)
echo   [PASS/FAIL] ops.scheduler_heartbeat —— 计划任务有没有按期执行
".venv\Scripts\python.exe" -X utf8 -m ttxt doctor
pause
