' 头条智能工厂面板 - 隐藏窗口启动(无黑窗)
' 双击本文件即可;停止方式:任务管理器结束 pythonw.exe,或运行 scripts\stop-dashboard.cmd
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\ttxt"
sh.Run """D:\ttxt\.venv\Scripts\pythonw.exe"" -X utf8 -m ttxt serve --port 8765", 0, False
WScript.Sleep 3000
sh.Run "http://127.0.0.1:8765/", 1, False
