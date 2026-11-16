@echo off
chcp 65001 >nul
cd /d D:\ttxt
echo 正在请求托管优雅停止:创建 supervisor_stop 标志
echo 托管会在下一轮循环里终止它看护的 schedule/worker 子进程,并把退出码写进状态文件。
".venv\Scripts\python.exe" -X utf8 -m ttxt supervisor --stop
echo.
echo 几秒后确认:scripts\supervisor-status.cmd
echo 想再启动:scripts\start-supervisor.cmd(它会先清掉停止标志再拉起)
pause
