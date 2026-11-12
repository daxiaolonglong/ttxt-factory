@echo off
chcp 65001 >nul
echo 正在停止面板(pythonw + ttxt serve)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'ttxt serve' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo 已尝试停止;如仍占用 8765 端口,请在任务管理器确认。
pause
