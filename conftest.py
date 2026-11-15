"""仓库根 conftest:把 ``10_tests`` 的 C-8 测试基座挂成 initial conftest 钩子。

为什么需要这个文件(2026-11-15 实测根因):

pytest 只在 **initial conftest**(由命令行参数所在目录及其祖先在配置阶段加载)里调用
``pytest_configure``。当调用形式是 ``pytest``(无参数)或 ``pytest .`` 时,
``10_tests/conftest.py`` 要到 collection 阶段才被加载,它的 ``pytest_configure``
**不会被调用**。实测后果:basetemp 退回 pytest 默认值,而在本项目的受限执行环境里
``tempfile.gettempdir()`` 解析成仓库根(``C:\\Users\\...\\Temp`` 不可写,候选回落到
当前目录),于是临时目录落到 ``D:\\ttxt\\pytest-of-*``,C-8 的三重隔离
(临时目录限定 07_tmp、日志目录重定向、生产库前后指纹比对)同时失效,
表现为 ``test_p15_test_isolation``/``test_p7_dashboard`` 等互不相干的 FAIL。

这里只前转**配置期**钩子;夹具仍然由 ``10_tests/conftest.py`` 自己注册(它照常作为
该目录的 conftest 被收集),不会重复注册,也不改变任何测试语义。
``10_tests/conftest.py`` 自己带 ``--basetemp`` 早退保护,因此两次 ``pytest_configure``
(例如 ``pytest 10_tests`` 时根与子目录各一次)只会生效一次。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent / "10_tests" / "conftest.py"

if _HARNESS.is_file():
    _spec = importlib.util.spec_from_file_location("_ttxt_harness_conftest", _HARNESS)
    assert _spec is not None and _spec.loader is not None
    _harness = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_harness)

    # 只暴露配置期钩子;其余(session/autouse 夹具)由原 conftest 注册
    pytest_configure = _harness.pytest_configure
