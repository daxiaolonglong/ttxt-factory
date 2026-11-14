"""P1 冒烟测试:目录、配置、数据库、磁盘预算、日志落点。"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from ttxt import config, db, housekeep


def test_project_root_and_required_dirs():
    assert config.PROJECT_ROOT.as_posix() == "D:/ttxt"
    missing = [str(d) for d in config.REQUIRED_DIRS if not d.exists()]
    assert not missing, f"missing dirs: {missing}"


def test_config_load():
    cfg = config.load_config()
    assert cfg["version"] == 1
    assert cfg["project_root"] == "D:\\ttxt"
    assert cfg["strata"]["base_url"].startswith("http://127.0.0.1")
    assert cfg["dashboard"]["host"] == "127.0.0.1"


def test_registry_categories():
    registry = config.load_registry()
    slugs = {item["slug"] for item in registry["categories"]}
    assert slugs >= {"technology", "finance", "social", "history", "health"}


def test_all_configured_paths_within_project():
    assert config.paths_within_project() == []


def test_category_directories():
    for slug in config.load_config()["categories"]:
        base = config.CATEGORIES_DIR / slug
        assert base.is_dir(), f"missing category dir: {base}"
        for sub in ("rag", "keywords", "metrics"):
            assert (base / sub).is_dir(), f"missing {sub} in {base}"


def test_system_db_schema(tmp_path):
    path = db.init_system_db(tmp_path / "system.db")
    assert path.exists()
    con = sqlite3.connect(path)
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"categories", "sources", "crawl_jobs", "model_jobs", "system_log", "app_state"} <= tables
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 5
        version = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        assert version == str(db.SCHEMA_VERSION)
    finally:
        con.close()


def test_real_system_db_initialized(tmp_path):
    """核对生产 system.db 结构:必须在 tmp 副本上打开,生产库文件不得被连接。

    只读 URI 连接同样会在生产目录生成 ``-wal``/``-shm`` 边车(实测),所以 C-8 之后
    一律“复制→读副本”,生产目录在测试前后保持零边车。
    """
    path = db.system_db_path()
    if not path.exists():
        pytest.skip("system.db 未初始化,请先运行 `ttxt init`")

    def sidecars(directory: Path, name: str) -> list[str]:
        return sorted(item.name for item in directory.glob(name + "*") if item.name.endswith(("-wal", "-shm", "-journal")))

    before = sidecars(path.parent, path.name)
    copy_path = tmp_path / path.name
    shutil.copyfile(path, copy_path)
    con = sqlite3.connect(copy_path.as_uri() + "?mode=ro", uri=True)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM categories").fetchone()[0] >= 5
    finally:
        con.close()
    assert sidecars(path.parent, path.name) == before, "读取生产库副本时生产目录出现了新的 WAL 边车"


def test_housekeep_report_shape():
    report = housekeep.report()
    assert report["root"] == "D:\\ttxt"
    assert report["free_gb"] > 0
    assert {"control", "categories", "logs", "temp"} <= set(report["sizes_bytes"])
    assert {"temp_max_gb", "log_max_mb", "backup_keep"} <= set(report["caps"])


def test_logs_are_written_inside_project():
    from ttxt.logging import log_event, log_path

    log_event("info", "tests", "test.event", "log path check")
    path = log_path()
    assert path.exists()
    assert config.PROJECT_ROOT in path.parents


def test_disk_budget_config_sane():
    disk = config.load_config()["disk"]
    assert disk["project_budget_gb"] > 0
    assert disk["min_free_gb"] > 0
    assert disk["temp_max_gb"] <= disk["project_budget_gb"]
