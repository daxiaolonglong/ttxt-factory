"""测试基座:路径注入 + 生产环境隔离(C-8)。

五件事:
1. pytest 临时目录固定到 ``D:/ttxt/07_tmp/pytest_basetemp/pytest-<n>``,
   不再散落到仓库根(``pytest-of-*``)或系统 TEMP。命令行显式 ``--basetemp`` 优先。
2. autouse 夹具把 ``model_gate`` 的默认锁路径重定向到每个测试自己的 ``tmp_path``,
   并在测试前后断言生产 ``00_control/model_gate/gate.lock`` 从未被创建。
3. session 级 autouse 夹具把 ``cfg.LOG_DIR`` 重定向到 ``07_tmp`` 下的专用目录:
   pytest 期间的日志(含 gate.* 事件)只落临时目录,生产 ``09_logs`` 逐字节不变。
   **禁止用删除生产日志的方式换取“看起来干净”。**
4. session 级 autouse 夹具记录每一次 ``sqlite3.connect`` 的目标路径;会话结束时断言
   整个会话没有任何测试连接过生产 ``system.db`` / 类目 ``articles.db``(只读 URI 也算)。
5. session 级夹具对生产 ``system.db``、6 个类目 ``articles.db``、``00_control`` 配置、
   ``worker.lock`` 以及 ``09_logs`` 下全部日志做前后指纹比对;任何写入都让会话失败。

另提供可复用夹具 ``isolated_data_root``:把 ``00_control`` 与 ``01_categories`` 的库/配置
复制成 ``07_tmp`` 下的副本,并把 ``ttxt`` 的路径常量指向副本(面板类测试用它)。
"""

from __future__ import annotations

import gc
import hashlib
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(r"D:\ttxt")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASETEMP_ROOT = ROOT / "07_tmp" / "pytest_basetemp"
KEEP_BASETEMP_RUNS = 10

CATEGORY_SLUGS = ("technology", "finance", "health", "social", "history", "demo")
DATA_DIRS = (ROOT / "00_control", ROOT / "01_categories")
LOG_DIR_PRODUCTION = ROOT / "09_logs"

PROTECTED_DB = [
    ROOT / "00_control" / "system.db",
    ROOT / "01_categories" / "technology" / "articles.db",
    ROOT / "01_categories" / "finance" / "articles.db",
    ROOT / "01_categories" / "health" / "articles.db",
    ROOT / "01_categories" / "social" / "articles.db",
    ROOT / "01_categories" / "history" / "articles.db",
    ROOT / "01_categories" / "demo" / "articles.db",
]
PROTECTED_FILES = [
    ROOT / "00_control" / "config.json",
    ROOT / "00_control" / "category_registry.json",
    ROOT / "00_control" / "scheduler_state.json",
    ROOT / "00_control" / "worker.lock",
]
PROTECTED_DIR = ROOT / "00_control" / "model_gate"


def pytest_configure(config: pytest.Config) -> None:
    """未显式指定 --basetemp 时,把 pytest 临时根目录限定在 07_tmp 下。"""
    if getattr(config.option, "basetemp", None):
        return
    BASETEMP_ROOT.mkdir(parents=True, exist_ok=True)
    highest = -1
    stale: list[Path] = []
    for item in BASETEMP_ROOT.iterdir():
        match = re.fullmatch(r"pytest-(\d+)", item.name)
        if not match or not item.is_dir():
            continue
        highest = max(highest, int(match.group(1)))
        stale.append(item)
    for item in sorted(stale, key=lambda p: int(re.fullmatch(r"pytest-(\d+)", p.name).group(1)))[:-KEEP_BASETEMP_RUNS]:
        try:
            item.rename(item.with_name(item.name + ".purging"))
            _rmtree(item.with_name(item.name + ".purging"))
        except OSError:
            continue
    config.option.basetemp = str(BASETEMP_ROOT / f"pytest-{highest + 1}")


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _fingerprint(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"missing": True}
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    stat = path.stat()
    entry: dict[str, object] = {
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }
    # 日志必须同时比“字节数 + 行数 + sha256”:上一批只比 DB 哈希,漏掉了 196 行测试日志污染
    if path.suffix.lower() in (".jsonl", ".log", ".bak"):
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            entry["lines"] = sum(1 for _ in fh)
    return entry


def _log_state() -> dict[str, object]:
    """生产 ``09_logs`` 下全部文件(含 .bak)的指纹,不遗漏任何日志资产。"""
    if not LOG_DIR_PRODUCTION.exists():
        return {"missing": True}
    return {
        path.relative_to(ROOT).as_posix(): _fingerprint(path)
        for path in sorted(LOG_DIR_PRODUCTION.rglob("*"))
        if path.is_file()
    }


def _snapshot() -> dict[str, object]:
    data: dict[str, object] = {}
    for path in PROTECTED_DB + PROTECTED_FILES:
        data[path.relative_to(ROOT).as_posix()] = _fingerprint(path)
    data.update(_log_state())
    data["00_control/model_gate"] = sorted(p.name for p in PROTECTED_DIR.iterdir()) if PROTECTED_DIR.exists() else []
    data["wal_sidecars"] = sorted(
        p.relative_to(ROOT).as_posix()
        for base in [ROOT / "00_control"] + [ROOT / "01_categories" / s for s in CATEGORY_SLUGS]
        for p in base.glob("*.db*")
        if p.name.endswith(("-wal", "-shm", "-journal"))
    )
    return data


# --------------------------------------------------------------------------- 判定辅助


def normalize_db_target(database: object, uri: bool) -> str:
    """sqlite 连接目标 → 绝对路径(``file:///D:/...?mode=ro`` 去 schema、authority 与查询串)。"""
    from urllib.parse import unquote

    text = str(database)
    if uri or text.startswith("file:"):
        rest = text[len("file:"):] if text.startswith("file:") else text
        if rest.startswith("//"):
            rest = rest[2:]
        rest = unquote(rest.split("?", 1)[0])
        if len(rest) >= 3 and rest[0] == "/" and rest[2] == ":":
            rest = rest[1:]
        text = rest
    if text in (":memory:", ""):
        return text
    try:
        return Path(text).resolve().as_posix()
    except OSError:
        return Path(text).as_posix()


def opens_production_db(target: str) -> bool:
    """该连接目标是否落在生产 ``00_control`` / ``01_categories`` 下的 ``*.db``。"""
    if target in (":memory:", ""):
        return False
    path = Path(target)
    if path.suffix.lower() != ".db":
        return False
    return any(str(path).lower().startswith(str(base).lower()) for base in DATA_DIRS)


# --------------------------------------------------------------------------- session 夹具


@pytest.fixture(scope="session")
def session_db_opens() -> list[str]:
    """记录整个会话里每一次 ``sqlite3.connect`` 的目标(归一化绝对路径)。只记录不拦截。"""
    seen: list[str] = []
    original = sqlite3.connect

    def wrapper(*args, **kwargs):  # noqa: ANN002, ANN003 - 透传标准库签名
        database = args[0] if args else kwargs.get("database", ":memory:")
        seen.append(normalize_db_target(database, bool(kwargs.get("uri"))))
        return original(*args, **kwargs)

    sqlite3.connect = wrapper
    try:
        yield seen
    finally:
        sqlite3.connect = original


@pytest.fixture(scope="session")
def isolated_log_dir(tmp_path_factory: pytest.TempPathFactory):
    """把 ``cfg.LOG_DIR`` 重定向到 07_tmp 下的专用目录;生产 09_logs 前后逐字节一致。

    同时导出 ``TTXT_TEST_LOG_DIR`` 环境变量:pytest 的 monkeypatch 不会跨进程传播,
    测试拉起的子进程(p14 闸门锁竞争/崩溃演练)必须读这个变量自行重定向,
    否则子进程会把 gate.* 事件写进生产日志(实测全量一次就追加 7 行)。
    """
    import os

    from ttxt import config as cfg

    production_before = _log_state()
    mocker = pytest.MonkeyPatch()
    target = tmp_path_factory.mktemp("ttxt_logs")
    assert target.resolve() != LOG_DIR_PRODUCTION.resolve()
    mocker.setattr(cfg, "LOG_DIR", target)
    previous_env = os.environ.get("TTXT_TEST_LOG_DIR")
    os.environ["TTXT_TEST_LOG_DIR"] = str(target)
    try:
        yield target
    finally:
        if previous_env is None:
            os.environ.pop("TTXT_TEST_LOG_DIR", None)
        else:
            os.environ["TTXT_TEST_LOG_DIR"] = previous_env
        mocker.undo()
        gc.collect()
        production_after = _log_state()
        changed = [
            key for key in production_before
            if production_before[key] != production_after.get(key)
        ]
        added = sorted(set(production_after) - set(production_before))
        assert not changed and not added, (
            f"测试会话改动了生产日志:{changed=} {added=}\n"
            f"测试日志应落在:{target}\n"
            "若变化来自子进程,检查它是否读取 TTXT_TEST_LOG_DIR 并重定向 cfg.LOG_DIR。\n"
            "不允许通过删除/截断 09_logs 换取“看起来干净”。"
        )


@pytest.fixture(scope="session", autouse=True)
def production_state_untouched(session_db_opens, isolated_log_dir):
    """生产数据库、控制文件与生产日志在测试会话前后必须逐字节一致,且全程未连接生产库。

    快照前先 ``gc.collect()``:SQLite WAL 边车(``-wal``/``-shm``)只在“最后一个连接关闭”
    时才被清理;若连接对象还挂在引用环里(如 dashboard 请求处理器),边车会残留在生产目录。
    强制回收让“残留边车”与“真实写入”区分开:回收后仍存在的边车才是真正未清理的副作用。
    """
    before = _snapshot()
    yield before
    gc.collect()
    time.sleep(0.2)
    gc.collect()
    after = _snapshot()
    changed = [key for key in before if before[key] != after.get(key)]
    assert not changed, (
        f"测试改动了生产状态: {changed}\nbefore={before}\nafter={after}\n"
        "若只有 wal_sidecars 变化,说明有测试直接打开了生产库(只读也会生成 -wal/-shm),"
        "应改为读取 tmp_path 下的副本。"
    )
    offenders = sorted({target for target in session_db_opens if opens_production_db(target)})
    assert not offenders, (
        f"测试会话连接过生产数据库(包括只读 URI):{offenders}\n"
        "正确做法:复制到 07_tmp 下的副本再连接,或用 isolated_data_root 夹具把路径常量指向副本。"
    )
    outside = sorted({
        target for target in session_db_opens
        if target not in (":memory:", "") and ROOT / "07_tmp" not in Path(target).parents
    })
    assert not outside, f"测试连接了 07_tmp 之外的数据库:{outside}"


# --------------------------------------------------------------------------- 复用夹具


@pytest.fixture()
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``00_control`` / ``01_categories`` 的库与配置复制成 tmp 副本,并把 ttxt 路径指向副本。

    复制是逐字节 ``copyfile``(含热 WAL,若存在);生产库文件只被读,不被 SQLite 打开。
    """
    from ttxt import config as cfg
    from ttxt import db as ttxt_db

    control = tmp_path / "00_control"
    categories = tmp_path / "01_categories"
    control.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "category_registry.json", "scheduler_state.json"):
        source = ROOT / "00_control" / name
        if source.exists():
            shutil.copyfile(source, control / name)
    system_db = ROOT / "00_control" / "system.db"
    if system_db.exists():
        shutil.copyfile(system_db, control / "system.db")
    for source in sorted((ROOT / "01_categories").glob("*/articles.db")):
        target = categories / source.parent.name / "articles.db"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        wal = source.with_name("articles.db-wal")
        if wal.exists():
            shutil.copyfile(wal, target.with_name("articles.db-wal"))

    monkeypatch.setattr(cfg, "CONTROL_DIR", control)
    monkeypatch.setattr(cfg, "CONFIG_PATH", control / "config.json")
    monkeypatch.setattr(cfg, "REGISTRY_PATH", control / "category_registry.json")
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", categories)
    monkeypatch.setattr(ttxt_db, "CONTROL_DIR", control)
    monkeypatch.setattr(ttxt_db, "CATEGORIES_DIR", categories)
    return tmp_path


@pytest.fixture(autouse=True)
def model_gate_lock_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """默认闸门锁路径重定向到 tmp,生产 gate.lock 在任何测试前后都必须不存在。"""
    from ttxt.model_gate import gate

    production_lock = Path(gate.GATE_LOCK)
    assert not production_lock.exists(), f"生产闸门锁已存在,先确认持有者再跑测试: {production_lock}"
    monkeypatch.setattr(gate, "GATE_LOCK", tmp_path / "gate.lock")
    started = time.monotonic()
    yield
    if not production_lock.exists():
        return
    gate.GATE_LOCK = production_lock
    pytest.fail(
        f"测试创建了生产闸门锁 {production_lock}(耗时 {time.monotonic() - started:.2f}s),"
        "说明该测试没有走临时锁路径。"
    )
