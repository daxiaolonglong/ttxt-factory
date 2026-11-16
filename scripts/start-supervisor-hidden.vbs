' 头条智能工厂 · 无人值守托管 - 隐藏窗口拉起(无黑窗)
' 只拉起 `ttxt supervisor --ensure`:没在跑才起一份,已在跑什么都不做。
' 被托管的子进程:schedule --daemon 与 worker(绝不包含写作/发布/模型)。
' 停止方式:运行 scripts\stop-supervisor.cmd(创建 supervisor_stop 标志,托管会优雅收尾)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\ttxt"
sh.Run """D:\ttxt\.venv\Scripts\pythonw.exe"" -X utf8 -m ttxt supervisor --ensure", 0, False
