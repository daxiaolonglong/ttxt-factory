@echo off
chcp 65001 >nul
echo ==========================================================
echo  安装无人值守兜底任务(P31)
echo  任务名:ttxt-supervisor-watchdog
echo  每 5 分钟跑一次 `ttxt supervisor --ensure`:
echo    - 托管进程活着 --ensure 什么都不做(单实例靠 supervisor.lock 区域锁)
echo    - 托管进程死了才拉起新的一份,不会同时存在两份
echo  限制(实测,不隐瞒):非管理员只能建"Interactive only"任务,
echo  所以只在已登录状态下自动恢复;注销/未登录不会运行。
echo  开机登录即启动需要管理员权限,见下面的可选命令。
echo ==========================================================
schtasks /create /tn "ttxt-supervisor-watchdog" /tr "D:\ttxt\.venv\Scripts\pythonw.exe -X utf8 -m ttxt supervisor --ensure" /sc daily /st 00:01 /ri 5 /du 23:59 /f
echo.
echo 查看任务:schtasks /query /tn "ttxt-supervisor-watchdog" /v /fo LIST
echo 立刻试一次:schtasks /run /tn "ttxt-supervisor-watchdog"
echo.
echo 可选(必须以管理员身份运行本脚本,非管理员实测 Access denied):
echo   schtasks /create /tn "ttxt-supervisor-logon" /tr "D:\ttxt\.venv\Scripts\pythonw.exe -X utf8 -m ttxt supervisor --ensure" /sc onlogon /f
echo.
echo 卸载:scripts\uninstall-supervisor-tasks.cmd
pause
