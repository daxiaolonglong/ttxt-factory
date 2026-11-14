"""C-8 收尾:测试隔离自证(生产日志 + 生产数据库)。

修复前这组测试应当失败(实测:测试把日志追加进 ``09_logs``,面板测试连接生产库);
修复后必须长期通过,作为回归护栏。

机制:
- conftest 的 session 级 autouse 夹具把 ``cfg.LOG_DIR`` 重定向到 ``07_tmp`` 下的专用目录,
  并记录整个会话里每一次 ``sqlite3.connect`` 的目标路径。
- 本文件只做判定与断言:判定用纯函数,断言比对生产文件的 bytes/行数/sha256。
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ttxt import config as cfg
from ttxt import db as ttxt_db
from ttxt import logging as ttxt_logging
from ttxt.model_gate import gate, queue

ROOT = Path(r"D:\ttxt")
PRODUCTION_LOG_DIR = ROOT / "09_logs"
TEST_TMP_ROOT = ROOT / "07_tmp"
PRODUCTION_DB_DIRS = (ROOT / "00_control", ROOT / "01_categories")


# --------------------------------------------------------------------------- 判定与指纹

def normalize_db_target(database: object, uri: bool) -> str:
    """把 sqlite 连接目标归一化成绝对路径(``file:///D:/...?mode=ro`` 去 schema/authority/查询串)。"""
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
    """纯判定:该连接目标是否命中生产 ``00_control`` / ``01_categories`` 下的 ``*.db``。"""
    if target in (":memory:", ""):
        return False
    path = Path(target)
    if path.suffix.lower() != ".db":
        return False
    return any(str(path).lower().startswith(str(base).lower()) for base in PRODUCTION_DB_DIRS)


def production_log_state() -> dict[str, dict]:
    """生产 ``09_logs`` 下每个文件的 bytes / 行数 / sha256(只读文件,不打开数据库)。"""
    state: dict[str, dict] = {}
    for path in sorted(PRODUCTION_LOG_DIR.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            lines = sum(1 for _ in fh)
        state[path.relative_to(ROOT).as_posix()] = {
            "bytes": path.stat().st_size,
            "lines": lines,
            "sha256": digest.hexdigest(),
        }
    return state


def production_db_fingerprints() -> dict[str, str]:
    state: dict[str, str] = {}
    for base in PRODUCTION_DB_DIRS:
        for path in sorted(base.rglob("*.db")):
            digest = hashlib.sha256()
            with path.open("rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(block)
            state[path.relative_to(ROOT).as_posix()] = f"{path.stat().st_size}:{digest.hexdigest()}"
    return state


def production_sidecars() -> list[str]:
    return sorted(
        path.relative_to(ROOT).as_posix()
        for base in PRODUCTION_DB_DIRS
        for path in base.rglob("*")
        if path.is_file() and path.name.endswith(("-wal", "-shm", "-journal"))
    )


def bad_targets(targets: list[str]) -> list[str]:
    return [target for target in targets if opens_production_db(target)]


# --------------------------------------------------------------------------- 夹具

@pytest.fixture()
def db_opens(request: pytest.FixtureRequest):
    """返回 ``since()``:本次测试开始之后记录到的 sqlite 连接目标(会话级记录器的切片)。"""
    seen: list[str] = request.getfixturevalue("session_db_opens")
    marker = len(seen)

    def since() -> list[str]:
        return seen[marker:]

    return since


# --------------------------------------------------------------------------- 临时目录

def test_pytest_temp_root_lives_under_07_tmp(tmp_path: Path) -> None:
    """所有 pytest 临时目录(含测试用数据库)必须落在 07_tmp 下。"""
    resolved = tmp_path.resolve()
    assert TEST_TMP_ROOT in resolved.parents, f"tmp_path 越界: {resolved}"


# --------------------------------------------------------------------------- 生产日志

def test_log_target_is_a_dedicated_directory_under_07_tmp() -> None:
    """测试会话内 ``cfg.LOG_DIR`` 必须是 07_tmp 下的专用目录,不能是生产 ``09_logs``。"""
    active = Path(cfg.LOG_DIR).resolve()
    assert TEST_TMP_ROOT in active.parents, f"LOG_DIR 指向生产日志目录:{active}"
    assert active != PRODUCTION_LOG_DIR.resolve()
    assert Path(ttxt_logging.log_path()).resolve().parent == active


def test_log_event_appends_to_test_log_dir_not_production() -> None:
    """写一条探针日志:必须落到测试日志目录,且生产日志逐字节不变。"""
    before = production_log_state()
    ttxt_logging.log_event("info", "c8.isolation", "c8.probe", "probe must land in the test log dir")
    after = production_log_state()
    assert before == after, f"log_event 改动了生产日志:\nbefore={before}\nafter={after}"
    probe_file = Path(ttxt_logging.log_path()).resolve()
    assert TEST_TMP_ROOT in probe_file.parents, f"探针日志写到了生产目录:{probe_file}"
    assert probe_file.exists() and "c8.probe" in probe_file.read_text(encoding="utf-8")


def test_all_pytest_log_lines_accumulate_in_the_temp_log_dir() -> None:
    """连续写两条:测试日志文件行数 +2,生产日志目录文件集合与内容都不变。"""
    temp_log = Path(ttxt_logging.log_path()).resolve()
    before_lines = len(temp_log.read_text(encoding="utf-8").splitlines()) if temp_log.exists() else 0
    before_state = production_log_state()
    before_names = sorted(path.name for path in PRODUCTION_LOG_DIR.iterdir())
    for index in (1, 2):
        ttxt_logging.log_event("warn", "c8.isolation", "c8.accumulate.probe", f"probe {index}")
    after_lines = len(temp_log.read_text(encoding="utf-8").splitlines())
    assert after_lines >= before_lines + 2, f"测试日志没有落在 {temp_log}"
    text = temp_log.read_text(encoding="utf-8")
    assert text.count("c8.accumulate.probe") >= 2, f"探针没有写进测试日志:{temp_log}"
    assert sorted(path.name for path in PRODUCTION_LOG_DIR.iterdir()) == before_names, (
        "生产日志目录出现新文件(轮转副作用泄漏)"
    )
    assert production_log_state() == before_state, "生产日志内容发生变化"


# --------------------------------------------------------------------------- 生产数据库

def test_session_recorder_captures_connections_and_flags_production(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """会话级记录器必须有效(自带探针),并且到目前为止没有任何测试打开过生产数据库。"""
    seen: list[str] = request.getfixturevalue("session_db_opens")
    marker = len(seen)
    probe_db = tmp_path / "recorder-probe.db"
    con = sqlite3.connect(probe_db)
    try:
        con.execute("CREATE TABLE probe (id INTEGER)")
    finally:
        con.close()
    assert seen[marker:], "记录器没有捕获到本次探针连接,隔离护栏本身失效"
    assert not bad_targets(seen), f"以下测试打开过生产数据库:{sorted(set(bad_targets(seen)))}"
    for target in seen:
        if target in (":memory:", ""):
            continue
        assert TEST_TMP_ROOT in Path(target).parents, f"07_tmp 之外的数据库连接:{target}"


def test_queue_and_gate_only_open_temp_system_db(tmp_path: Path, db_opens) -> None:
    """复现 P5/P14 的队列+闸门调用路径:期间的 sqlite 连接不得命中生产库,也不得越出 07_tmp。"""
    db_path = ttxt_db.init_system_db(tmp_path / "system.db")
    lock_path = tmp_path / "gate.lock"
    job_id = queue.enqueue("chat", {"prompt": "isolation"}, category="demo", db_path=db_path)
    with gate.FileLock(lock_path, timeout_s=5.0):
        row = queue.claim_next(db_path=db_path)
        assert row is not None and row["id"] == job_id
        queue.finish(job_id, {"text": "ok"}, db_path=db_path)
    opened = db_opens()
    assert opened, "记录器没有捕获到本次队列连接,隔离测试本身失效"
    assert not bad_targets(opened), f"打开过生产数据库:{bad_targets(opened)}"
    for target in opened:
        if target in (":memory:", ""):
            continue
        assert TEST_TMP_ROOT in Path(target).parents, f"连接了 07_tmp 之外的数据库:{target}"


_CHILD_DEFAULT = r'''
import sys
sys.path.insert(0, r"D:\ttxt")
from ttxt import config as cfg
print(cfg.LOG_DIR)
'''

_CHILD_ISOLATED = r'''
import os
import sys
from pathlib import Path

sys.path.insert(0, r"D:\ttxt")
log_dir = os.environ.get("TTXT_TEST_LOG_DIR")
if not log_dir:
    print("MISSING_ENV")
    raise SystemExit(2)
from ttxt import config as cfg
cfg.LOG_DIR = Path(log_dir)
from ttxt.logging import log_event
log_event("info", "c8.isolation", "c8.child.probe", "child log must stay in 07_tmp")
print(cfg.LOG_DIR)
'''


def _run_child(script: Path, tmp_path: Path) -> tuple[int, str, str]:
    """跑子进程:stdout/stderr 落文件,不用管道(受限时 pipe stdio 会 EPERM)。"""
    out_path = tmp_path / (script.name + ".out")
    err_path = tmp_path / (script.name + ".err")
    with out_path.open("wb") as out, err_path.open("wb") as err:
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(script)],
            cwd=str(tmp_path),
            stdout=out,
            stderr=err,
            timeout=90,
        )
    return (
        completed.returncode,
        out_path.read_text(encoding="utf-8", errors="replace").strip(),
        err_path.read_text(encoding="utf-8", errors="replace").strip(),
    )


def test_child_processes_must_honor_the_log_redirect(tmp_path: Path, isolated_log_dir: Path) -> None:
    """子进程不继承 monkeypatch:必须靠 TTXT_TEST_LOG_DIR 重定向,否则就写进生产 09_logs。"""
    # ① 取证:子进程眼里默认 LOG_DIR 就是生产目录 → 重定向只能由测试自己完成(只打印,不写日志)
    default_script = tmp_path / "child_default.py"
    default_script.write_text(_CHILD_DEFAULT, encoding="utf-8")
    code, stdout, stderr = _run_child(default_script, tmp_path)
    assert code == 0, f"子进程默认值探测失败:{stderr}"
    assert Path(stdout).resolve() == PRODUCTION_LOG_DIR.resolve(), (
        f"子进程默认 LOG_DIR 不是生产目录,说明本测试的取证前提变了:{stdout}"
    )

    # ② 带引导的子进程只写测试日志目录,生产日志逐字节不变
    before = production_log_state()
    isolated_script = tmp_path / "child_isolated.py"
    isolated_script.write_text(_CHILD_ISOLATED, encoding="utf-8")
    code, stdout, stderr = _run_child(isolated_script, tmp_path)
    assert code == 0, f"隔离子进程失败:{stderr}"
    assert Path(stdout).resolve() == Path(isolated_log_dir).resolve(), (
        f"子进程没有拿到测试日志目录:{stdout}"
    )
    assert production_log_state() == before, "子进程把日志写进了生产 09_logs"
    temp_log = Path(ttxt_logging.log_path()).resolve()
    assert "c8.child.probe" in temp_log.read_text(encoding="utf-8"), (
        f"子进程日志没有落到 {temp_log}"
    )


def test_production_db_untouched_around_copy_read(tmp_path: Path, db_opens) -> None:
    """按 C-8 规定的方式核对生产库:复制→读副本;生产库字节与边车都不变。"""
    source = ttxt_db.system_db_path()
    before = production_db_fingerprints()
    before_sidecars = production_sidecars()
    copy_path = tmp_path / "system-copy.db"
    shutil.copyfile(source, copy_path)
    con = sqlite3.connect(copy_path)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM model_jobs").fetchone()[0] >= 0
    finally:
        con.close()
    assert not bad_targets(db_opens()), f"读副本时打开了生产库:{bad_targets(db_opens())}"
    assert production_db_fingerprints() == before, "生产数据库字节/大小发生变化"
    assert production_sidecars() == before_sidecars, "生产目录残留了 WAL/SHM 边车"


def test_production_db_detector_is_sound() -> None:
    """判定函数本身:识别生产库与只读 URI,放过临时库/内存库/非库文件(无副作用)。"""
    production = normalize_db_target(ROOT / "00_control" / "system.db", False)
    production_uri = normalize_db_target(
        (ROOT / "00_control" / "system.db").as_uri() + "?mode=ro", True
    )
    category = normalize_db_target(ROOT / "01_categories" / "demo" / "articles.db", False)
    temp = normalize_db_target(TEST_TMP_ROOT / "pytest_basetemp" / "pytest-9" / "system.db", False)
    assert opens_production_db(production)
    assert opens_production_db(production_uri)
    assert opens_production_db(category)
    assert not opens_production_db(temp)
    assert not opens_production_db(":memory:")
    assert not opens_production_db(str(TEST_TMP_ROOT / "not-a-db.txt"))
