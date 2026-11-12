@echo off
chcp 65001 >nul
cd /d D:\ttxt
echo ==========================================
echo  头条智能工厂面板
echo  http://127.0.0.1:8765/
echo  按 Ctrl+C 或关闭本窗口即停止
echo ==========================================
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8765/"
".venv\Scripts\python.exe" -X utf8 -m ttxt serve --port 8765
echo.
echo 面板已停止。
pause
