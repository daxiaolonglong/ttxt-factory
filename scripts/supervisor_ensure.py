# -*- coding: utf-8 -*-
"""计划任务用的幂等拉起入口(与 cwd 无关)。

为什么要有这个包装:Windows 计划任务的 ``/tr`` 不能设工作目录(非管理员),任务默认工作目录是
``C:\\Windows\\System32``,于是 ``pythonw -m ttxt ...`` 找不到 ``ttxt`` 包 —— 实测退出码 1、
托管进程一个都没起来。本包装自己把项目根加进 ``sys.path``,所以在任何目录下都能跑。

pythonw 没有 stdout/stderr,这里把它们指向 devnull,免得 ``print`` 在无控制台解释器里出岔子;
真正的动作与结果都落在 ``00_control/supervisor_state.json`` 和 ``09_logs`` 里。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 无控制台解释器下把标准流重定向到 devnull(结果以状态文件与日志为准,不靠标准输出)
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from ttxt.__main__ import main  # noqa: E402  (必须在 sys.path 修好之后导入)

if __name__ == "__main__":
    raise SystemExit(main(["supervisor", "--ensure"]))
