@echo off
chcp 65001 >nul
echo 删除无人值守兜底任务(任务不存在时会报"不能找到任务",属正常)
schtasks /delete /tn "ttxt-supervisor-watchdog" /f
schtasks /delete /tn "ttxt-supervisor-logon" /f
echo.
echo 注意:删任务不等于停托管。停托管跑 scripts\stop-supervisor.cmd
pause
