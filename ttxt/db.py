"""SQLite 访问层:P1 负责 system.db v1(system 级表)。类目 articles.db 在 P3 建立。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import (
    CATEGORIES_DIR,
    CONTROL_DIR,
    load_registry,
    now_iso,
)

SCHEMA_VERSION = 1

SYSTEM_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  db_path TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL DEFAULT 'all',
  name TEXT NOT NULL,
  url TEXT,
  kind TEXT NOT NULL DEFAULT 'web',
  enabled INTEGER NOT NULL DEFAULT 1,
  robots_ok INTEGER NOT NULL DEFAULT 0,
  rate_limit_s REAL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crawl_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_type TEXT NOT NULL,
  category TEXT,
  keyword TEXT,
  source TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  last_error TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  owner TEXT,
  lease_expires_at TEXT,
  meta_json TEXT
);
CREATE TABLE IF NOT EXISTS model_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT,
  job_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  result_json TEXT
);
CREATE TABLE IF NOT EXISTS system_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  level TEXT NOT NULL,
  module TEXT NOT NULL,
  event TEXT NOT NULL,
  message TEXT,
  meta_json TEXT
);
CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_status ON crawl_jobs(status);
CREATE INDEX IF NOT EXISTS idx_model_jobs_status ON model_jobs(status);
"""


def system_db_path() -> Path:
    return CONTROL_DIR / "system.db"


def connect(path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    ensure_crawl_job_columns(con)
    return con


def ensure_crawl_job_columns(con) -> None:
    """P12 幂等迁移:凡含 crawl_jobs 的库都补齐 Worker 租约列(绝不重建/丢数据)。

    放在 connect() 里执行,保证任何入口(CLI/Worker/面板/测试)都不会因为
    ``init_system_db`` 未被调用而撞上 ``no such column: lease_expires_at``。
    """
    has_table = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='crawl_jobs'"
    ).fetchone()
    if not has_table:
        return
    cols = {row["name"] for row in con.execute("PRAGMA table_info(crawl_jobs)")}
    changed = False
    if "owner" not in cols:
        con.execute("ALTER TABLE crawl_jobs ADD COLUMN owner TEXT")
        changed = True
    if "lease_expires_at" not in cols:
        con.execute("ALTER TABLE crawl_jobs ADD COLUMN lease_expires_at TEXT")
        changed = True
    if changed:
        con.commit()


def init_system_db(path: Path | str | None = None) -> Path:
    """幂等初始化 system.db:建表、写入 schema 版本、按注册表种入类目。"""
    target = Path(path) if path is not None else system_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    con = connect(target)
    try:
        con.executescript(SYSTEM_SCHEMA)
        # P12 幂等迁移:旧库补 Worker 租约列(connect 内已保证,这里显式再调一次以求自证)
        ensure_crawl_job_columns(con)
        con.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        stamp = now_iso()
        for item in load_registry().get("categories", []):
            db_path = str(CATEGORIES_DIR / item["slug"] / "articles.db")
            con.execute(
                """
                INSERT INTO categories(slug, display_name, db_path, status, created_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                  display_name = excluded.display_name,
                  db_path = excluded.db_path
                """,
                (
                    item["slug"],
                    item["display_name"],
                    db_path,
                    item.get("status", "active"),
                    stamp,
                ),
            )
        con.commit()
    finally:
        con.close()
    return target
