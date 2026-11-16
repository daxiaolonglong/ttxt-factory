@echo off
chcp 65001 >nul
cd /d D:\ttxt
echo ==========================================
echo  头条智能工厂 · 无人值守托管(P31)
echo  托管对象只有两个:ttxt schedule --daemon、ttxt worker
echo  绝不托管:写作 / 发布 / 模型调用(设计上限死)
echo  本命令幂等:已经有托管在跑就什么都不做,不会起第二份
echo ==========================================
echo 先清掉停止标志(双击本脚本就是人明确要求跑起来)
".venv\Scripts\python.exe" -X utf8 -m ttxt supervisor --clear-stop
".venv\Scripts\python.exe" -X utf8 -m ttxt supervisor --ensure
echo.
echo 当前状态:
".venv\Scripts\python.exe" -X utf8 -m ttxt supervisor --status
echo.
echo 日志:09_logs\supervisor.out.log / supervisor.err.log,子进程日志在 09_logs\ttxt-YYYYMMDD.jsonl
echo 停止:scripts\stop-supervisor.cmd
pause
