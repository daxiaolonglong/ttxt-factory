"""R41:草稿 → 发布链接 → T+N 表现数据的绑定链路。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ttxt.crawler import store as crawler_store
from ttxt.writing import publication


def _iso(offset_hours: float) -> str:
    return (datetime.now().astimezone() + timedelta(hours=offset_hours)).isoformat(timespec="seconds")


def _db(tmp_path):
    return crawler_store.migrate_articles_db("r41", db_path=tmp_path / "articles.db")


def _insert_article(db_path, *, title: str, url: str, published_at: str | None) -> int:
    from ttxt.db import connect

    con = connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO articles(category, source, title, content, published_at, url,"
            " url_normalized, fetched_at, content_hash) VALUES(?,?,?,?,?,?,?,?,?)",
            ("r41", "test", title, "正文" * 50, published_at, url, url, _iso(0), f"hash-{title}"),
        )
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def _insert_snapshot(db_path, article_id: int, captured_at: str, **metrics) -> None:
    from ttxt.db import connect

    con = connect(db_path)
    try:
        con.execute(
            "INSERT INTO metric_snapshots(article_id, captured_at, reads, likes, favorites,"
            " comments, impressions, data_source) VALUES(?,?,?,?,?,?,?,?)",
            (
                article_id,
                captured_at,
                metrics.get("reads"),
                metrics.get("likes"),
                metrics.get("favorites"),
                metrics.get("comments"),
                metrics.get("impressions"),
                "manual_verified",
            ),
        )
        con.commit()
    finally:
        con.close()


def test_window_days_default_and_env(monkeypatch):
    monkeypatch.delenv(publication.WINDOW_DAYS_ENV, raising=False)
    assert publication.window_days() == 3
    monkeypatch.setenv(publication.WINDOW_DAYS_ENV, "7")
    assert publication.window_days() == 7
    monkeypatch.setenv(publication.WINDOW_DAYS_ENV, "not-a-number")
    assert publication.window_days() == 3


def test_bind_refuses_unparseable_publish_time(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ValueError):
        publication.bind("r41", "d1", "https://example.com/a", "", db_path=db)
    with pytest.raises(ValueError):
        publication.bind("r41", "d1", "https://example.com/a", "11月12日", db_path=db)


def test_bind_pending_then_on_time(tmp_path):
    db = _db(tmp_path)
    pending = publication.bind(
        "r41", "d2", "https://example.com/b", _iso(-24), db_path=db
    )
    assert pending["status"] == publication.STATUS_PENDING
    assert pending["reads"] is None

    on_time_pub = publication.bind(
        "r41", "d2b", "https://example.com/b2", _iso(-96), db_path=db
    )
    on_time = publication.record_metrics(
        "r41",
        on_time_pub["publication_id"],
        {"reads": 120, "likes": 7, "comments": 2, "impressions": 900},
        captured_at=_iso(-23),  # T+3d+1h
        data_source="manual_verified",
        db_path=db,
    )
    assert on_time["status"] == publication.STATUS_ON_TIME
    assert on_time["reads"] == 120
    assert on_time["favorites"] is None  # 缺失如实为 None,不是 0
    assert "favorites" in on_time["missing_fields"]


def test_late_and_overdue(tmp_path):
    db = _db(tmp_path)
    late_pub = publication.bind("r41", "d3", "https://example.com/c", _iso(-240), db_path=db)
    assert late_pub["status"] == publication.STATUS_UNAVAILABLE  # 无任何观测 ⇒ 不可用,不是 overdue
    late = publication.record_metrics(
        "r41", late_pub["publication_id"], {"reads": 10},
        captured_at=_iso(-120), data_source="manual_verified", db_path=db,
    )
    assert late["status"] == publication.STATUS_LATE
    assert late["reads"] == 10

    overdue_pub = publication.bind("r41", "d4", "https://example.com/d", _iso(-240), db_path=db)
    overdue = publication.record_metrics(
        "r41", overdue_pub["publication_id"], {"reads": 5},
        captured_at=_iso(-216), data_source="manual_verified", db_path=db,
    )
    assert overdue["status"] == publication.STATUS_OVERDUE
    assert overdue["reads"] is None  # 窗口前的观测不冒充 T+N 读数


def test_early_when_only_pre_due_observations(tmp_path):
    db = _db(tmp_path)
    pub = publication.bind("r41", "d5", "https://example.com/e", _iso(-1), db_path=db)
    early = publication.record_metrics(
        "r41", pub["publication_id"], {"reads": 3},
        captured_at=_iso(-0.5), data_source="manual_verified", db_path=db,
    )
    assert early["status"] == publication.STATUS_EARLY
    assert early["reads"] is None


def test_record_rejects_all_missing_and_unknown_source(tmp_path):
    db = _db(tmp_path)
    pub = publication.bind("r41", "d6", "https://example.com/f", _iso(-1), db_path=db)
    with pytest.raises(ValueError):
        publication.record_metrics(
            "r41", pub["publication_id"], {"reads": None, "likes": None},
            data_source="manual_verified", db_path=db,
        )
    with pytest.raises(ValueError):
        publication.record_metrics(
            "r41", pub["publication_id"], {"reads": 1}, data_source="", db_path=db
        )
    with pytest.raises(PermissionError):
        publication.record_metrics(
            "r41", pub["publication_id"], {"reads": 1}, data_source="not_authorized", db_path=db
        )


def test_observation_upsert_never_wipes_known_value(tmp_path):
    db = _db(tmp_path)
    pub = publication.bind("r41", "d7", "https://example.com/g", _iso(-96), db_path=db)
    publication.record_metrics(
        "r41", pub["publication_id"], {"reads": 50, "likes": 4},
        captured_at=_iso(-23), data_source="manual_verified", db_path=db,
    )
    merged = publication.record_metrics(
        "r41", pub["publication_id"], {"reads": None, "comments": 3},
        captured_at=_iso(-23), data_source="manual_verified", db_path=db,
    )
    assert merged["reads"] == 50  # 空值不抹掉已知值
    assert merged["comments"] == 3


def test_backfill_articles_reports_status_and_missing(tmp_path):
    db = _db(tmp_path)
    on_time_id = _insert_article(db, title="on-time", url="https://example.com/t1", published_at=_iso(-120))
    early_id = _insert_article(db, title="early", url="https://example.com/t2", published_at=_iso(-24))
    _insert_snapshot(db, on_time_id, _iso(-47), reads=100, likes=5, comments=1, impressions=1000)
    _insert_snapshot(db, early_id, _iso(-2), reads=7)

    report = publication.backfill_articles("r41", apply=True, db_path=db)
    assert report["apply"] is True
    assert report["articles_with_published_at"] == 2
    assert report["status_counts"][publication.STATUS_ON_TIME] == 1
    assert report["status_counts"][publication.STATUS_EARLY] == 1
    assert report["missing_counts"]["favorites"] == 2
    assert report["windows_written"] == 2


def test_report_returns_chain_per_publication(tmp_path):
    db = _db(tmp_path)
    pub = publication.bind(
        "r41", "d8", "https://example.com/h", _iso(-96), platform="toutiao", db_path=db
    )
    publication.record_metrics(
        "r41", pub["publication_id"], {"reads": 66, "likes": 2, "comments": 1, "impressions": 800},
        captured_at=_iso(-23), data_source="manual_verified", db_path=db,
    )
    result = publication.report("r41", draft_id="d8", db_path=db)
    assert result["count"] == 1
    entry = result["publications"][0]
    assert entry["url"] == "https://example.com/h"
    assert entry["published_at"]
    assert entry["status"] == publication.STATUS_ON_TIME
    assert entry["reads"] == 66
    assert entry["favorites"] is None
    assert "favorites" in entry["missing_fields"]


def test_chain_usability_names_the_draft_breakpoint(tmp_path):
    db = _db(tmp_path)
    from ttxt.db import connect

    con = connect(db)
    try:
        con.execute(
            "INSERT INTO writing_history(draft_id, category, topic, version, content, model_id,"
            " created_at, status) VALUES(?,?,?,?,?,?,?,?)",
            ("d1", "r41", "选题", 1, "草稿", "local", _iso(0), "draft"),
        )
        con.commit()
    finally:
        con.close()

    report = publication.chain_usability("r41", db_path=db)
    assert report["counts"]["drafts_total"] == 1
    assert report["counts"]["drafts_bound"] == 0
    draft_step = report["breakpoints"][0]
    assert draft_step["step"] == "draft → publication"
    assert draft_step["status"] == "broken"
    assert "draft url" in draft_step["missing_fields"]
