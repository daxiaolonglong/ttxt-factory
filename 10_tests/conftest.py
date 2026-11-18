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
6. session 级夹具把 ``cfg.CATEGORIES_DIR`` / ``ttxt_db.CATEGORIES_DIR`` 默认重定向到
   ``07_tmp`` 下的空目录:测试里任何"按类目找库"的代码(采集入口串起的清洗管线最容易踩)
   都只会写临时目录,生产 ``01_categories`` 默认不可达。

另提供可复用夹具 ``isolated_data_root``:把 ``00_control`` 与 ``01_categories`` 的库/配置
复制成 ``07_tmp`` 下的副本,并把 ``ttxt`` 的路径常量指向副本(面板类测试用它)。
"""

from __future__ import annotations

import gc
import hashlib
import os
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
_OWNED_BASETEMP = False  # set True when pytest_configure took ownership of --basetemp
# 规则 17/27:临时目录必须有上限并回收,磁盘不得膨胀。留 4 份足够事后取证
# (最近一轮 + 失败复现用),更早的轮次证据都在 09_logs 与 08_wiki/reports 里。
KEEP_BASETEMP_RUNS = 4
# P38(2026-11-17 实测根因):**按份数保留管不住体积**。T-14 把 social 扩到 1.7 GB 之后,
# 夹具每测复制整库 ⇒ 单轮 basetemp 实测 28 GB(老注释写的"约 50MB/轮"早已失真),
# 留 4 份就是 56 GB,`07_tmp` 因此把 scratch 限额(2 GB)顶穿 28 倍。
# 所以现在两条同时生效:份数上限 + **字节上限**,并在磁盘不够时当场拒绝开跑,而不是跑爆盘。
# P41(2026-11-18 实测):**把字节上限本身顶穿 scratch 限额**——老值 4.0 GB 比 config里的
# ``disk.scratch_max_gb`` 2 GB 大,因此把 conftest 压到 4.0 GB 之后 doctor 把仍红
# (实测 07_tmp=3.798 GB:运行目录 5 份 3,063,521,084 字节 + pytest_trim 死份 219,041,368 字节)。
# 预算把**从 config里的读 scratch 上限再留边**(最新份之外把仍压到 2 份,日志、取证件坐盘),
# 两口径不得矛盾;测末清把也压,老值把只跑开把压。
SCRATCH_ROOT = ROOT / "07_tmp"
SAFETY_BYTES = 64 * 1024 * 1024  # 把 logs/receipts 把 growth 把 during  a  run 把 in  account
MIN_BASETEMP_BUDGET_BYTES = 512 * 1024 * 1024
MIN_FREE_GB_AT_START = 8.0
# 类目库隔离副本改为"会话级裁剪副本":整库逐字节复制是 28 GB 的来源,裁剪副本只保留
# 最近 N 篇及其同簇文章(聚类/近重复语义仍在),按生产库 size+mtime 做缓存键,跨会话复用。
TRIM_DIR = ROOT / "07_tmp" / "pytest_trim"
TRIM_ARTICLE_ROWS = 400
# 裁剪逻辑版本:改裁剪规则(比如新增要清的非内容表)必须 +1,否则旧缓存会按
# "生产库 size+mtime 没变"直接复用,新规则等于没生效。R42 起 = 2(清 ``metrics_due_queue``)。
TRIM_LOGIC_VERSION = 2
TEST_COPY_MAX_MB = 40.0
TEST_COPY_BUDGET_MB = 200.0

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
    # ``00_control/scheduler_state.json`` 不在受保护清单里:P31 起无人值守调度每个周期都要重写
    # 心跳文件,字节数与 mtime 必然变化,按内容哈希比对会稳定误报(实测 sha256 相同、只差 mtime)。
    # 按规则 51 的同一处理方式,它的守卫放在 doctor 的 ``ops.scheduler_heartbeat`` 与托管自身测试里。
    ROOT / "00_control" / "worker.lock",
]
PROTECTED_DIR = ROOT / "00_control" / "model_gate"


def _dir_size(path: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(base) / name).stat().st_size
            except OSError:
                continue
    return total


def scratch_cap_bytes() -> int:
    """The scratch cap ``doctor`` enforces for all of 07_tmp, read from config."""
    try:
        from ttxt import config as _cfg

        cap_gb = float(_cfg.load_config().get("disk", {}).get("scratch_max_gb", 2.0))
    except Exception:  # noqa: BLE001 - a config gap must not turn the test harness red
        cap_gb = 2.0
    return int(cap_gb * 1024**3)


def basetemp_budget_bytes() -> int:
    """P41: budget = scratch cap minus what 07_tmp already holds outside basetemp (trim
    caches, run logs, reclaim receipts) minus a safety margin, so the cap that ``doctor``
    enforces for all of 07_tmp can never be contradicted by this harness's own budget.
    """
    other = max(0, _dir_size(SCRATCH_ROOT) - _dir_size(BASETEMP_ROOT))
    return max(MIN_BASETEMP_BUDGET_BYTES, scratch_cap_bytes() - other - SAFETY_BYTES)


def pytest_configure(config: pytest.Config) -> None:
    """未显式指定 --basetemp 时,把 pytest 临时根目录限定在 07_tmp 下。"""
    if getattr(config.option, "basetemp", None):
        return
    global _OWNED_BASETEMP
    _OWNED_BASETEMP = True
    BASETEMP_ROOT.mkdir(parents=True, exist_ok=True)
    highest = -1
    stale: list[Path] = []
    for item in BASETEMP_ROOT.iterdir():
        match = re.fullmatch(r"pytest-(\d+)", item.name)
        if not match or not item.is_dir():
            continue
        highest = max(highest, int(match.group(1)))
        stale.append(item)
    # 规则 29:任何自动删除必须逐条打日志,不得静默。
    purged: list[tuple[str, int]] = []
    for item in sorted(stale, key=lambda p: int(re.fullmatch(r"pytest-(\d+)", p.name).group(1)))[:-KEEP_BASETEMP_RUNS]:
        victim = item.with_name(item.name + ".purging")
        try:
            item.rename(victim)
            size = _dir_size(victim)
            _rmtree(victim)
            purged.append((item.name, size))
        except OSError:
            continue
    for name, size in purged:
        message = (
            f"basetemp.purged: 已回收旧临时运行目录 07_tmp/pytest_basetemp/{name}"
            f"({size:,} 字节),按保留份数只留最近 {KEEP_BASETEMP_RUNS} 份"
        )
        _announce(config, message)
    # P38:份数上限之外再上一道**字节**闸门——体积才是真凶,份数只是表象。
    budget = basetemp_budget_bytes()
    by_bytes = _purge_basetemp_to_budget(budget)
    for name, size in by_bytes:
        _announce(
            config,
            f"basetemp.purged_by_bytes: 已回收 07_tmp/pytest_basetemp/{name}({size:,} 字节),"
            f"运行目录总量必须压到 {budget / 1024**3:.2f} GB 以内(P38/P41)",
        )
    for name, size in _purge_trim_caches(1):
        _announce(
            config,
            f"trim_cache.purged: 已回收 07_tmp/pytest_trim/{name}"
            f"({size:,} 字节),每类目只最新份可命中(缓存键含生产库 size+mtime_ns)",
        )
    config.option.basetemp = str(BASETEMP_ROOT / f"pytest-{highest + 1}")
    _require_free_disk(config)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """P41: purge at session end too — a budget only enforced at configure time let one
    suite run leave 856 MB behind and ``doctor`` still reported ``disk.scratch`` FAIL.
    The newest run dir (this run's own evidence) is always kept.
    """
    if not _OWNED_BASETEMP:
        return
    budget = basetemp_budget_bytes()
    for name, size in _purge_basetemp_to_budget(budget):
        message = (
            f"basetemp.purged_by_bytes: 已回收 07_tmp/pytest_basetemp/{name}"
            f"({size:,} 字节),运行目录总量必须压到 {budget / 1024**3:.2f} GB 以内(P38/P41)"
        )
        try:
            session.config.issue_config_time_warning(UserWarning(message), stacklevel=2)
        except Exception:  # noqa: BLE001
            print(message, file=sys.stderr)


def _announce(config: pytest.Config, message: str) -> None:
    """自动删除必须逐条可见(规则 29):能走 pytest 告警就走,否则落 stderr,绝不静默。"""
    try:
        config.issue_config_time_warning(UserWarning(message), stacklevel=2)
    except Exception:  # pragma: no cover - 兜底,不得静默
        print(message, file=sys.stderr)


_TRIM_CACHE_NAME = re.compile(r"^(.+)-\d+-\d+\.db$")


def _purge_trim_caches(keep_generations: int = 1) -> list[tuple[str, int]]:
    """每类目只最新份可命中,老份只坐盘。"""
    if not TRIM_DIR.is_dir():
        return []
    by_slug: dict[str, list[tuple[int, Path]]] = {}
    for item in TRIM_DIR.iterdir():
        if not item.is_file():
            continue
        match = _TRIM_CACHE_NAME.match(item.name)
        if not match:
            continue
        try:
            stamp = item.stat().st_mtime_ns
        except OSError:
            continue
        by_slug.setdefault(match.group(1), []).append((stamp, item))
    purged: list[tuple[str, int]] = []
    keep = max(1, int(keep_generations))
    for _slug, items in by_slug.items():
        items.sort(key=lambda entry: entry[0], reverse=True)
        for _stamp, old_db in items[keep:]:
            side = old_db.with_name(old_db.name + ".lineage.json")
            targets = [side, old_db] if side.is_file() else [old_db]
            for target in targets:
                try:
                    size = target.stat().st_size
                except OSError:
                    continue
                staging = target.with_name(target.name + ".purging")
                try:
                    target.rename(staging)
                    staging.unlink(missing_ok=True)
                    purged.append((target.name, size))
                except OSError:
                    continue
    return purged


STRAY_ROOT_PATTERNS = (re.compile(r"tmp[A-Za-z0-9_]{8}"), re.compile(r"pytest-of-.+"))


def _root_stray_dirs(root: Path) -> set[str]:
    """仓库根下"看起来就是临时残留"的一级目录名(tempfile 默认名、pytest 老 basetemp)。"""
    return {
        item.name
        for item in root.iterdir()
        if item.is_dir() and any(pat.fullmatch(item.name) for pat in STRAY_ROOT_PATTERNS)
    }


def _purge_basetemp_to_budget(
    budget_bytes: int | None = None, keep_newest: int = 1
) -> list[tuple[str, int]]:
    """按最旧优先回收 basetemp 运行目录,直到总量压进字节预算;被占用的跳过并回报。"""
    limit = int(budget_bytes) if budget_bytes is not None else basetemp_budget_bytes()
    if not BASETEMP_ROOT.exists():
        return []
    runs: list[tuple[float, int, Path]] = []
    for item in BASETEMP_ROOT.iterdir():
        if not item.is_dir():
            continue
        try:
            runs.append((item.stat().st_mtime, _dir_size(item), item))
        except OSError:
            continue
    total = sum(size for _mtime, size, _p in runs)
    purged: list[tuple[str, int]] = []
    ordered = sorted(runs, key=lambda entry: entry[0])
    keep = max(0, int(keep_newest))
    protected = {entry[2].name for entry in ordered[len(ordered) - keep:]} if keep else set()
    for _mtime, size, item in ordered:
        if total <= limit:
            break
        if item.name in protected:
            continue
        victim = item.with_name(item.name + ".purging")
        try:
            item.rename(victim)
            _rmtree(victim)
            purged.append((item.name, size))
            total -= size
        except OSError:
            continue
    return purged


def _require_free_disk(config: pytest.Config) -> None:
    """空闲盘不足时**拒绝开跑**,并把能跑通的回收命令写清楚。

    实测教训(P38):basetemp 涨到 56 GB 时 D: 只剩 34 GB,再跑一轮全量就会把盘写爆,
    而失败现场是"互不相干的测试随机红"——排查成本远高于一句提前拒绝。
    """
    floor = MIN_FREE_GB_AT_START
    try:
        from ttxt import config as cfg

        floor = max(floor, float(cfg.load_config().get("disk", {}).get("min_free_gb", 5)))
    except Exception:  # pragma: no cover - 配置读不出来时仍要用底线值
        pass
    free = shutil.disk_usage(str(ROOT)).free / 1024**3
    if free >= floor:
        return
    pytest.exit(
        f"磁盘空闲 {free:.1f} GB 低于下限 {floor:g} GB,拒绝开跑测试(P38 防线)。\n"
        f"先回收临时运行目录,再重跑:\n"
        f"  python -m ttxt housekeep --reclaim-scratch --reclaim-apply --reclaim-keep-runs 0\n"
        f"回收清单可先 dry-run:去掉 --reclaim-apply。取证目录(07_tmp 根下的往轮账本)不在回收范围。",
        returncode=1,
    )


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _fingerprint(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"missing": True}
    stat = path.stat()
    # 区域锁文件(``*.lock``)在活着的 Worker/托管进程持有 msvcrt 排它锁时连本进程都读不出来
    # (PermissionError),而测试会话现在默认与无人值守托管并存 ⇒ 只能按 stat 比对。
    # 这仍然能抓到"测试改写了生产锁文件"(字节数或 mtime 变了就报),只是不比内容哈希。
    if path.name.lower().endswith(".lock"):
        return {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": None, "locked_ok": True}
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
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


@pytest.fixture(scope="session")
def session_categories_dir(tmp_path_factory: pytest.TempPathFactory):
    """会话级把类目根重定向到 07_tmp 下的空目录,生产 ``01_categories`` 默认不可达。

    实测根因(C-4):采集入口现在会串起清洗管线(``runner._execute_job`` 调
    ``pipeline.run_pipeline(category)``)。老测试里凡是只伪造 ``insert_items``、
    却没有重定向类目根的,管线就会直接打开并改写生产 ``01_categories/<slug>/articles.db``
    ——文章行没动,但派生字段被测试写进了生产库,而且这类副作用只在会话结束时才被发现。
    把"类目根默认在 07_tmp 下"做成会话级默认,而不是逐个测试补 patch:新测试忘记隔离
    最多是把自己的库写在临时目录,不会再污染生产。
    需要断言真实仓库布局的测试(如 p1 冒烟)应显式对着 ``config.PROJECT_ROOT`` 断言。
    """
    from ttxt import config as cfg
    from ttxt import db as ttxt_db

    mocker = pytest.MonkeyPatch()
    target = tmp_path_factory.mktemp("session_categories")
    production = ROOT / "01_categories"
    assert target.resolve() != production.resolve(), "类目根重定向没有生效"
    mocker.setattr(cfg, "CATEGORIES_DIR", target)
    mocker.setattr(ttxt_db, "CATEGORIES_DIR", target)
    try:
        yield target
    finally:
        mocker.undo()


@pytest.fixture(scope="session", autouse=True)
def no_stray_temp_at_repo_root():
    """临时文件必须待在 07_tmp,不许在仓库根造 `tmp<8位>` / `pytest-of-*` 目录(P38)。

    仓库根实测残留过 3 个 `tmp********`(里面全是临时 SQLite)和一个 53 MB 的
    `pytest-of-yangl`:那是更早的 conftest 把 TMPDIR 指到项目根造成的。今天没有这条
    重定向,但缺了守卫就等于没人证明它没回来。
    """
    before = _root_stray_dirs(ROOT)
    yield
    new = sorted(_root_stray_dirs(ROOT) - before)
    assert not new, (
        f"测试会话在仓库根新建了临时目录:{new}\n"
        "正确做法:用 pytest 的 tmp_path/tmp_path_factory,或在 07_tmp 下自建目录;"
        "不许改 TMPDIR 到项目根,也不许在仓库根 mkdtemp。"
    )


@pytest.fixture(scope="session", autouse=True)
def production_state_untouched(session_db_opens, isolated_log_dir, session_categories_dir):
    """生产数据库、控制文件与生产日志在测试会话前后必须逐字节一致,且全程未连接生产库。

    快照前先 ``gc.collect()``:SQLite WAL 边车(``-wal``/``-shm``)只在“最后一个连接关闭”
    时才被清理;若连接对象还挂在引用环里(如 dashboard 请求处理器),边车会残留在生产目录。
    强制回收让“残留边车”与“真实写入”区分开:回收后仍存在的边车才是真正未清理的副作用。

    P24 根因修正:边车**不能**参与逐字节一致性比较。只读巡检脚本(``mode=ro``)打不开
    checkpoint,SQLite 会在生产目录留下 ``-wal``/``-shm``,下一次可写连接才收掉;这类
    **会话开始前就存在**的边车与测试无关(2026-11-16 全量跑因此误报 1 error,
    _receipt:07_tmp/r27_metrics_expand/p24_full_suite.txt_)。真实风险由两条精确判定兜住:
    ①所有受保护文件的 sha256/行数必须一致(被改或被删都算失败);
    ②会话期间**新增**的边车才算违规(配合 ``session_db_opens`` 的连接白名单)。
    """
    before = _snapshot()
    yield before
    gc.collect()
    time.sleep(0.2)
    gc.collect()
    after = _snapshot()
    missing = [key for key in before if key not in after]
    changed = [
        key for key in before
        if key != "wal_sidecars" and before[key] != after.get(key)
    ]
    assert not missing and not changed, (
        f"测试改动了生产状态: 变更={changed} 缺失={missing}\nbefore={before}\nafter={after}\n"
        "受保护文件(库/配置/注册表/日志/model_gate)的内容或行数必须逐字节一致。"
    )
    new_sidecars = sorted(set(after.get("wal_sidecars", [])) - set(before.get("wal_sidecars", [])))
    assert not new_sidecars, (
        f"测试会话在生产目录新建了 SQLite 边车:{new_sidecars}\n"
        "说明有测试直接打开了生产库(只读也会生成 -wal/-shm),"
        "应改为读取 07_tmp 下的副本,或用 isolated_data_root 夹具把路径常量指向副本。\n"
        f"会话开始前的边车:{before.get('wal_sidecars')}\n会话结束时的边车:{after.get('wal_sidecars')}"
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _trim_cache_paths(source: Path) -> tuple[Path, Path]:
    """缓存键 = 生产库的 ``size + mtime_ns``:生产库一变,缓存自动失效,不会读到旧数据。"""
    st = source.stat()
    cache = TRIM_DIR / f"{source.parent.name}-{st.st_size}-{st.st_mtime_ns}.db"
    return cache, cache.with_name(cache.name + ".lineage.json")


def _drop_staging(staging: Path) -> None:
    """半成品必须清干净:残留的 ``.building`` 既占盘,又会被误当成缓存。"""
    for path in (staging, *[staging.with_name(staging.name + s) for s in ("-wal", "-shm")]):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _trim_with_sql(staging: Path, rows: int) -> tuple[int, int]:
    """在副本上裁剪:最近 N 篇 + 同簇成员全量,级联清掉孤儿派生行,小表整体保留。

    保留 ``articles_meta`` 很关键——`viral_calibration`(爆款阈值)就住在这里,
    裁剪掉它,面板与 doctor 的读数会变成"未标定"的假象。
    """
    from ttxt.analysis import rag

    con = sqlite3.connect(staging)
    try:
        con.execute("PRAGMA temp_store=MEMORY")
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        total = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        con.execute("CREATE TEMP TABLE kept_ids(id INTEGER PRIMARY KEY)")
        con.execute(
            "INSERT OR IGNORE INTO temp.kept_ids(id) SELECT id FROM articles "
            "ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT ?",
            (int(rows),),
        )
        # 同簇成员必须整簇保留:只留半簇会让聚类/近重复的断言出现假绿假红。
        con.execute("CREATE TEMP TABLE kept_clusters(cluster_id TEXT PRIMARY KEY)")
        con.execute(
            "INSERT OR IGNORE INTO temp.kept_clusters(cluster_id) "
            "SELECT DISTINCT cluster_id FROM articles "
            "WHERE id IN (SELECT id FROM temp.kept_ids) AND cluster_id IS NOT NULL AND cluster_id <> ''"
        )
        con.execute(
            "INSERT OR IGNORE INTO temp.kept_ids(id) "
            "SELECT id FROM articles WHERE cluster_id IN (SELECT cluster_id FROM temp.kept_clusters)"
        )
        kept = con.execute("SELECT COUNT(*) FROM temp.kept_ids").fetchone()[0]
        con.execute("DELETE FROM articles WHERE id NOT IN (SELECT id FROM temp.kept_ids)")
        # R42:``metrics_due_queue`` 是取数排队表(每篇文章一行),不是内容表;
        # 不跟着裁剪会让类目库副本因排队行超 40 MB 上限,而排队状态对测试语义为零。
        for table in ("viral_labels", "article_metrics", "metric_snapshots", "metrics_due_queue"):
            if table in tables:
                con.execute(f"DELETE FROM {table} WHERE article_id NOT IN (SELECT id FROM temp.kept_ids)")
        if "evidence_links" in tables:
            con.execute(
                "DELETE FROM evidence_links WHERE article_id IS NOT NULL "
                "AND article_id NOT IN (SELECT id FROM temp.kept_ids)"
            )
        if "article_clusters" in tables:
            con.execute(
                "DELETE FROM article_clusters WHERE cluster_id NOT IN "
                "(SELECT DISTINCT cluster_id FROM articles WHERE cluster_id IS NOT NULL)"
            )
        con.commit()
    finally:
        con.close()
    rag.rebuild_index(source_slug(staging), db_path=staging)
    con = sqlite3.connect(staging)
    try:
        # VACUUM 必须排在 FTS 重建之后:全量 FTS 索引(实测 social 811 MiB 空闲页)
        # 只有在它被删掉之后再 VACUUM,文件才会真正缩小。
        con.execute("PRAGMA temp_store=MEMORY")
        con.execute("VACUUM")
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.commit()
    finally:
        con.close()
    return total, kept


def source_slug(db_path: Path) -> str:
    """裁剪缓存文件名 ``<slug>-<size>-<mtime>.db`` 与 ``<slug>/articles.db`` 都反推 slug。"""
    name = db_path.parent.name
    return name if name != "pytest_trim" else db_path.name.split("-", 1)[0]


def build_trimmed_category_db(source: Path, rows: int = TRIM_ARTICLE_ROWS) -> dict[str, object]:
    """生产类目库 → ``07_tmp/pytest_trim`` 下的裁剪副本(逐字节复制后才裁剪,可跨会话复用)。

    生产库只被 ``shutil.copyfile`` 读字节,**不经 SQLite 连接** ⇒ 不会在生产目录留下
    ``-wal/-shm``,也不违反"测试不得连接生产库"的红线;裁剪与 FTS 重建都发生在副本上。
    """
    import json

    cache, lineage_file = _trim_cache_paths(source)
    if cache.exists() and lineage_file.exists():
        try:
            record = json.loads(lineage_file.read_text(encoding="utf-8"))
            if (
                Path(record["cache"]).resolve() == cache.resolve()
                and int(record.get("trim_version") or 0) == TRIM_LOGIC_VERSION
            ):
                return record
        except (OSError, ValueError, KeyError):
            pass
    TRIM_DIR.mkdir(parents=True, exist_ok=True)
    staging = cache.with_name(cache.name + ".building")
    if staging.exists():
        staging.unlink()
    try:
        shutil.copyfile(source, staging)
        # 生产库若正处 WAL 且有未 checkpoint 的提交,只拷主文件会少行 ⇒ 边车一起拷,
        # 让 SQLite 在副本上自行回放(生产目录始终不动)。
        for suffix in ("-wal", "-shm"):
            sidecar = source.with_name(source.name + suffix)
            if sidecar.exists():
                try:
                    shutil.copyfile(sidecar, staging.with_name(staging.name + suffix))
                except OSError:
                    pass
        source_sha = _sha256_file(source)
        copy_sha = _sha256_file(staging)
        if source_sha != copy_sha:
            raise RuntimeError(
                f"裁剪副本的字节复制与生产库不一致,拒绝继续:{source}(源={source_sha[:12]} 副本={copy_sha[:12]})"
            )
        total, kept = _trim_with_sql(staging, rows)
        os.replace(staging, cache)
    except BaseException:
        _drop_staging(staging)
        raise
    record: dict[str, object] = {
        "slug": source.parent.name,
        "source": source.resolve().as_posix(),
        "source_bytes": source.stat().st_size,
        "source_mtime_ns": source.stat().st_mtime_ns,
        "source_sha256": source_sha,
        "rows_source": total,
        "rows_kept": kept,
        "cache": cache.resolve().as_posix(),
        "cache_bytes": cache.stat().st_size,
        "trim_rows": int(rows),
        "trim_version": TRIM_LOGIC_VERSION,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    lineage_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


@pytest.fixture(scope="session")
def trim_caches() -> dict[str, dict[str, object]]:
    """会话级:为每个生产类目库准备一份裁剪副本,整个会话只做一次(实测每测一份整库=28 GB/轮)。"""
    lineage: dict[str, dict[str, object]] = {}
    for source in sorted((ROOT / "01_categories").glob("*/articles.db")):
        lineage[source.parent.name] = build_trimmed_category_db(source)
    return lineage


@pytest.fixture()
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trim_caches: dict[str, dict[str, object]]) -> Path:
    """把 ``00_control`` / 裁剪后的 ``01_categories`` 副本放进 tmp,并把 ttxt 路径指向副本。

    P38:类目库不再是整库逐字节复制,而是 ``trim_caches`` 里那份裁剪副本的逐字节复制。
    每测都带字节上限断言——一旦有人把副本改回整库,这里当场红,而不是让磁盘悄悄涨到 56 GB。
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

    copied_bytes = 0
    for slug, record in sorted(trim_caches.items()):
        cache = Path(record["cache"])
        size = cache.stat().st_size
        assert size <= TEST_COPY_MAX_MB * 1024 * 1024, (
            f"{slug} 的隔离副本 {size:,} 字节已超单库上限 {TEST_COPY_MAX_MB:g} MB。"
            "整库复制正是 28 GB/轮 的根因:副本必须是 07_tmp/pytest_trim 下的裁剪副本,"
            "要更多样本请调 TRIM_ARTICLE_ROWS,不要退回整库。"
        )
        target = categories / slug / "articles.db"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cache, target)
        copied_bytes += size
    assert copied_bytes <= TEST_COPY_BUDGET_MB * 1024 * 1024, (
        f"单测试的类目副本合计 {copied_bytes:,} 字节已超预算 {TEST_COPY_BUDGET_MB:g} MB(P38 字节预算)"
    )

    monkeypatch.setattr(cfg, "CONTROL_DIR", control)
    monkeypatch.setattr(cfg, "CONFIG_PATH", control / "config.json")
    monkeypatch.setattr(cfg, "REGISTRY_PATH", control / "category_registry.json")
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", categories)
    monkeypatch.setattr(ttxt_db, "CONTROL_DIR", control)
    monkeypatch.setattr(ttxt_db, "CATEGORIES_DIR", categories)
    return tmp_path


@pytest.fixture(autouse=True)
def browser_profile_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """任何测试都不得取生产浏览器 profile 的跨进程锁(P36)。

    生产 Worker 与调度进程共用 ``02_crawler/.browser/profile``,P36 起取锁上限 600 秒。
    测试一旦走生产路径,要么把正在跑的 Worker 卡住,要么自己卡 600 秒。
    逐测试把 ``fetcher.browser_profile_dir`` 换到 tmp 下,锁文件也跟着落到 tmp
    (锁在 profile 目录**之外**,由同一个函数派生)。

    注:生产锁文件在本轮开始前就存在是正常的(常驻进程正持有),因此只在
    "跑之前不存在、跑之后出现"时判定测试污染了生产路径。
    """
    from ttxt import config as cfg
    from ttxt.crawler import fetcher

    production_lock = cfg.CRAWLER_DIR / ".browser" / "profile.lock"
    had_lock_before = production_lock.exists()
    isolated_root = tmp_path / "browser-crawler"

    def isolated_profile() -> Path:
        target = isolated_root / ".browser" / "profile"
        target.mkdir(parents=True, exist_ok=True)
        return target

    monkeypatch.setattr(fetcher, "browser_profile_dir", isolated_profile)
    started = time.monotonic()
    yield
    if not had_lock_before and production_lock.exists():
        pytest.fail(
            f"测试创建了生产浏览器 profile 锁 {production_lock}"
            f"(耗时 {time.monotonic() - started:.2f}s),说明它没有走临时 profile 路径。"
        )


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
