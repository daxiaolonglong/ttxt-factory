"""类目 articles.db 写入与采集任务记录(P2 最小 schema)。"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from ttxt import config as cfg
from ttxt.crawler.clean import content_hash, normalize_url
from ttxt.crawler.extract import ExtractedItem
from ttxt.db import connect, system_db_path

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
PROCESSOR_VERSION = "p2-v1"
SCHEMA_VERSION = "p10-v1"

ARTICLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  source TEXT NOT NULL,
  source_item_id TEXT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  excerpt TEXT,
  author TEXT,
  published_at TEXT,
  url TEXT NOT NULL,
  url_normalized TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  completeness TEXT NOT NULL DEFAULT 'full',
  credibility TEXT NOT NULL DEFAULT 'unverified',
  processor_version TEXT NOT NULL DEFAULT 'p2-v1'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_url_norm ON articles(url_normalized);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE TABLE IF NOT EXISTS articles_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

CLUSTERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS article_clusters (
  cluster_id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  representative_id INTEGER NOT NULL,
  member_count INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

# P3 schema v2 新增列(ALTER TABLE 幂等添加)
P3_COLUMNS: list[tuple[str, str]] = [
    ("cleaned_content", "TEXT"),
    ("text_length", "INTEGER"),
    ("simhash", "TEXT"),
    ("cluster_id", "TEXT"),
    ("cleaned_at", "TEXT"),
    ("clean_version", "TEXT"),
]

V3_SCHEMA = """
CREATE TABLE IF NOT EXISTS keywords (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL,
  category TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'seed',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  UNIQUE(keyword, category)
);
CREATE TABLE IF NOT EXISTS keyword_performance (
  keyword_id INTEGER PRIMARY KEY,
  collected INTEGER NOT NULL DEFAULT 0,
  quality_articles INTEGER NOT NULL DEFAULT 0,
  avg_text_length REAL,
  score REAL,
  last_used_at TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(keyword_id) REFERENCES keywords(id)
);
CREATE TABLE IF NOT EXISTS viral_labels (
  article_id INTEGER PRIMARY KEY,
  label TEXT NOT NULL,
  confidence_tier TEXT NOT NULL,
  score REAL,
  reason TEXT,
  features_json TEXT,
  labeled_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS article_metrics (
  article_id INTEGER PRIMARY KEY,
  reads INTEGER,
  likes INTEGER,
  favorites INTEGER,
  comments INTEGER,
  impressions INTEGER,
  account_baseline TEXT,
  data_source TEXT NOT NULL,
  credibility TEXT NOT NULL DEFAULT 'unverified',
  captured_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  article_id INTEGER NOT NULL,
  captured_at TEXT NOT NULL,
  reads INTEGER,
  likes INTEGER,
  favorites INTEGER,
  comments INTEGER,
  impressions INTEGER,
  data_source TEXT
);
CREATE TABLE IF NOT EXISTS knowledge_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  kind TEXT NOT NULL,
  statement TEXT NOT NULL,
  evidence_json TEXT,
  support_count INTEGER NOT NULL DEFAULT 0,
  confidence TEXT NOT NULL DEFAULT 'exploratory',
  updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(article_id UNINDEXED, title, body);
"""

V4_SCHEMA = """
CREATE TABLE IF NOT EXISTS writing_history (
  draft_id TEXT NOT NULL,
  category TEXT NOT NULL,
  topic TEXT NOT NULL,
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  model_id TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (draft_id, version)
);
CREATE TABLE IF NOT EXISTS evaluation_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  evaluator TEXT NOT NULL,
  criteria_json TEXT,
  score REAL,
  issues_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id TEXT NOT NULL,
  claim TEXT NOT NULL,
  article_id INTEGER,
  quote TEXT,
  overlap REAL,
  credibility TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prediction_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  subject TEXT NOT NULL,
  predicted_score REAL,
  basis TEXT,
  predicted_at TEXT NOT NULL,
  outcome_score REAL,
  outcome_at TEXT,
  notes TEXT
);
"""


def migrate_articles_db(category: str, db_path: Path | None = None) -> Path:
    """P2 → P3 → P4 → P6:清洗/聚类列、分析表、FTS5 索引与写作表,幂等。"""
    target = init_articles_db(category, db_path=db_path)
    con = connect(target)
    try:
        existing = {row[1] for row in con.execute("PRAGMA table_info(articles)")}
        for name, ddl in P3_COLUMNS:
            if name not in existing:
                con.execute(f"ALTER TABLE articles ADD COLUMN {name} {ddl}")
        con.executescript(CLUSTERS_SCHEMA)
        con.executescript(V3_SCHEMA)
        con.executescript(V4_SCHEMA)
        writing_columns = {row[1] for row in con.execute("PRAGMA table_info(writing_history)")}
        if "status" not in writing_columns:
            con.execute("ALTER TABLE writing_history ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'")
        con.execute(
            "INSERT OR REPLACE INTO articles_meta(key, value) VALUES('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        con.commit()
    finally:
        con.close()
    return target


def category_dir(category: str) -> Path:
    if not SLUG_RE.match(category or ""):
        raise ValueError(f"invalid category slug: {category!r}")
    return cfg.CATEGORIES_DIR / category


def articles_db_path(category: str) -> Path:
    return category_dir(category) / "articles.db"


def init_articles_db(category: str, db_path: Path | None = None) -> Path:
    target = Path(db_path) if db_path is not None else articles_db_path(category)
    target.parent.mkdir(parents=True, exist_ok=True)
    con = connect(target)
    try:
        con.executescript(ARTICLES_SCHEMA)
        con.execute(
            "INSERT OR REPLACE INTO articles_meta(key, value) VALUES('schema_version', ?)",
            (PROCESSOR_VERSION,),
        )
        con.commit()
    finally:
        con.close()
    return target


def insert_items(
    category: str,
    source_id: str,
    items: list[ExtractedItem],
    db_path: Path | None = None,
) -> dict[str, int]:
    target = Path(db_path) if db_path is not None else init_articles_db(category)
    con = connect(target)
    inserted = duplicates = 0
    fetched_at = cfg.now_iso()
    try:
        for item in items:
            url_normalized = normalize_url(item.url)
            hash_value = content_hash(item.title, item.content)
            cursor = con.execute(
                """
                INSERT OR IGNORE INTO articles(
                  category, source, source_item_id, title, content, excerpt, author,
                  published_at, url, url_normalized, fetched_at, content_hash,
                  completeness, credibility, processor_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    category,
                    source_id,
                    item.source_item_id,
                    item.title,
                    item.content,
                    item.excerpt,
                    item.author,
                    item.published_at,
                    item.url,
                    url_normalized,
                    fetched_at,
                    hash_value,
                    "full" if item.content else "excerpt",
                    "unverified",
                    PROCESSOR_VERSION,
                ),
            )
            if cursor.rowcount:
                inserted += 1
            else:
                duplicates += 1
        con.commit()
    finally:
        con.close()
    return {"inserted": inserted, "duplicates": duplicates}


def count_articles(category: str, db_path: Path | None = None) -> int:
    target = Path(db_path) if db_path is not None else articles_db_path(category)
    if not target.exists():
        return 0
    con = connect(target)
    try:
        return int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
    finally:
        con.close()


# ---- 采集任务记录(system.db.crawl_jobs) ----


def create_crawl_job(source_id: str, category: str, db_path: Path | None = None) -> int:
    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        cursor = con.execute(
            "INSERT INTO crawl_jobs(job_type, category, source, status, created_at) VALUES('crawl', ?, ?, 'queued', ?)",
            (category, source_id, cfg.now_iso()),
        )
        con.commit()
        return int(cursor.lastrowid)
    finally:
        con.close()


def mark_job_started(job_id: int, db_path: Path | None = None) -> None:
    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        con.execute(
            "UPDATE crawl_jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=?",
            (cfg.now_iso(), job_id),
        )
        con.commit()
    finally:
        con.close()


def finish_crawl_job(
    job_id: int,
    status: str,
    error: str | None = None,
    meta: dict | None = None,
    db_path: Path | None = None,
) -> None:
    import json

    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        con.execute(
            "UPDATE crawl_jobs SET status=?, last_error=?, finished_at=?, meta_json=? WHERE id=?",
            (status, error, cfg.now_iso(), json.dumps(meta or {}, ensure_ascii=False), job_id),
        )
        con.commit()
    finally:
        con.close()


def fail_crawl_job(job_id: int, error: str, meta: dict | None = None, db_path: Path | None = None) -> str:
    """任务失败:attempts 未达上限则重新排队(queued),否则 failed。"""
    import json

    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        row = con.execute(
            "SELECT attempts, max_attempts FROM crawl_jobs WHERE id=?", (job_id,)
        ).fetchone()
        attempts = int(row["attempts"]) if row else 0
        max_attempts = int(row["max_attempts"]) if row else 3
        status = "queued" if attempts < max_attempts else "failed"
        con.execute(
            "UPDATE crawl_jobs SET status=?, last_error=?, finished_at=?, meta_json=? WHERE id=?",
            (
                status,
                error[:2000],
                cfg.now_iso() if status == "failed" else None,
                json.dumps(meta or {}, ensure_ascii=False),
                job_id,
            ),
        )
        con.commit()
        return status
    finally:
        con.close()


def requeue_failed_crawl_jobs(db_path: Path | None = None) -> int:
    """把 attempts 未达上限的 failed 采集任务重新排队(等待人工/--run 重跑)。"""
    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        cursor = con.execute(
            "UPDATE crawl_jobs SET status='queued', finished_at=NULL "
            "WHERE status='failed' AND attempts < max_attempts"
        )
        con.commit()
        return int(cursor.rowcount)
    finally:
        con.close()


def recover_stale_crawl_jobs(lease_s: float = 1800.0, db_path: Path | None = None) -> int:
    """进程中断恢复:running 超过租约 → failed(根因:进程中断)。"""
    from datetime import datetime

    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        rows = con.execute("SELECT id, started_at FROM crawl_jobs WHERE status='running'").fetchall()
        now = datetime.now().astimezone()
        recovered = 0
        for row in rows:
            started = row["started_at"]
            if not started:
                continue
            try:
                started_dt = datetime.fromisoformat(started)
            except ValueError:
                continue
            if (now - started_dt).total_seconds() > lease_s:
                con.execute(
                    "UPDATE crawl_jobs SET status='failed', finished_at=?, "
                    "last_error='recovered: 进程中断(运行超过租约)' WHERE id=?",
                    (cfg.now_iso(), row["id"]),
                )
                recovered += 1
        con.commit()
        return recovered
    finally:
        con.close()
