"""P1 冒烟测试:目录、配置、数据库、磁盘预算、日志落点。"""

import sqlite3

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


def test_real_system_db_initialized():
    path = db.system_db_path()
    if not path.exists():
        pytest.skip("system.db 未初始化,请先运行 `ttxt init`")
    con = sqlite3.connect(path)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM categories").fetchone()[0] >= 5
    finally:
        con.close()


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
