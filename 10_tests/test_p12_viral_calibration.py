"""P12 批次5:爆款校准诚实口径测试(样本不足→不改口径;样本足→分位拟合)。

注意:本文件中的指标全部是**合成测试夹具**(fixture),仅验证机制,
不构成任何真实爆款结论;真实库中无授权指标时一律 unscored/missing_data。
"""

from __future__ import annotations

import json

import pytest

from ttxt import config as cfg, db
from ttxt.analysis import metrics_import, viral
from ttxt.crawler import store


@pytest.fixture
def cat_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", tmp_path / "cats")
    db_path = tmp_path / "cats" / "demo" / "articles.db"
    store.migrate_articles_db("demo", db_path=db_path)
    return db_path


def _seed_articles(db_path, n: int) -> None:
    con = db.connect(db_path)
    try:
        for i in range(n):
            con.execute(
                "INSERT INTO articles(category, source, source_item_id, title, content, url, url_normalized,"
                " content_hash, fetched_at, completeness, text_length)"
                " VALUES('demo', 'fixture', ?, ?, ?, ?, ?, ?, ?, 'full', ?)",
                (
                    f"fx-{i}",
                    f"标题{i}",
                    "内容" * 500,
                    f"https://e.com/{i}",
                    f"https://e.com/{i}",
                    f"h{i}",
                    "2026-11-13T00:00:00+08:00",
                    2000,
                ),
            )
        con.commit()
    finally:
        con.close()


def _seed_metrics(db_path, n: int) -> None:
    con = db.connect(db_path)
    try:
        for i in range(1, n + 1):
            con.execute(
                "INSERT OR REPLACE INTO article_metrics(article_id, reads, likes, favorites, comments,"
                " impressions, data_source, credibility, captured_at) VALUES(?,?,?,?,?,?,?,?, 'fixture_src')",
                (i, 900 + i, 90 + i, 40 + i, 10 + i, 10000, "self_reported", "2026-11-13T00:00:00+08:00"),
            )
        con.commit()
    finally:
        con.close()


def _seed_reads_metrics(db_path, article_ids, *, base_reads: int = 1000) -> None:
    """只给阅读量 + 点赞 + 评论,**不给曝光量**;用于 T-25 的 measured_reads 池。"""
    con = db.connect(db_path)
    try:
        for article_id in article_ids:
            reads = base_reads + article_id * 13
            con.execute(
                "INSERT OR REPLACE INTO article_metrics(article_id, reads, likes, favorites, comments,"
                " impressions, data_source, credibility, captured_at) VALUES(?,?,?,NULL,?,NULL,?,?, 'fixture_src')",
                (article_id, reads, 20 + article_id * 3, 1 + article_id, "self_reported",
                 "2026-11-13T00:00:00+08:00"),
            )
        con.commit()
    finally:
        con.close()


def test_no_metrics_all_unscored_missing(cat_db):
    _seed_articles(cat_db, 10)
    result = viral.label_category("demo", db_path=cat_db)
    assert result["tiers"] == {"missing_data": 10}
    assert result["calibration"] == {"status": "default_thresholds", "sample": 0}


def test_calibration_refuses_small_sample(cat_db):
    _seed_articles(cat_db, 5)
    _seed_metrics(cat_db, 5)
    viral.label_category("demo", db_path=cat_db)
    out = viral.calibrate("demo", db_path=cat_db)
    assert out["status"] == "insufficient_sample"
    assert out["sample"] == 5
    # 口径未被改动:标签仍是默认阈值 provenance
    con = db.connect(cat_db)
    reason = con.execute("SELECT reason FROM viral_labels LIMIT 1").fetchone()[0]
    assert "默认阈值(未校准" in reason
    assert con.execute("SELECT COUNT(*) FROM articles_meta WHERE key='viral_calibration'").fetchone()[0] == 0
    con.close()


def test_calibration_fits_and_relabels(cat_db):
    n = viral.MIN_CALIBRATION_SAMPLE + 10
    _seed_articles(cat_db, n)
    _seed_metrics(cat_db, n)
    viral.label_category("demo", db_path=cat_db)
    out = viral.calibrate("demo", db_path=cat_db)
    assert out["status"] == "calibrated"
    assert out["sample"] == n
    ths = out["thresholds"]
    assert ths[0] >= ths[1] >= ths[2]
    relabel = viral.label_category("demo", db_path=cat_db)
    assert relabel["calibration"]["status"] == "calibrated"
    con = db.connect(cat_db)
    reasons = {r[0] for r in con.execute("SELECT DISTINCT reason FROM viral_labels")}
    assert reasons == {"真实曝光与互动数据 + 校准阈值"}
    con.close()


def test_incomplete_metrics_never_zero_filled(cat_db):
    _seed_articles(cat_db, 2)
    con = db.connect(cat_db)
    con.execute(
        "INSERT INTO article_metrics(article_id, reads, likes, favorites, comments, impressions,"
        " data_source, credibility, captured_at) VALUES(1, 100, NULL, NULL, NULL, 1000, 'fixture_src', 'self_reported', '2026-11-13T00:00:00+08:00')"
    )
    con.commit()
    con.close()
    viral.label_category("demo", db_path=cat_db)
    con = db.connect(cat_db)
    tier, reason = con.execute(
        "SELECT confidence_tier, reason FROM viral_labels WHERE article_id=1"
    ).fetchone()
    assert tier == "incomplete_data"
    assert "缺失不等于 0" in reason
    con.close()


def test_reads_pool_scores_without_impressions_and_calibrates_separately(cat_db):
    """T-25:有阅读量、无曝光量 ⇒ measured_reads 独立池可评分,阈值单独标定。"""
    n = viral.MIN_CALIBRATION_SAMPLE + 10
    _seed_articles(cat_db, n)
    _seed_reads_metrics(cat_db, range(1, n + 1))
    result = viral.label_category("demo", db_path=cat_db)
    assert result["tiers"] == {viral.TIER_MEASURED_READS: n}
    assert result["calibration"] == {"status": "default_thresholds", "sample": 0}
    assert result["calibration_reads"] == {"status": "default_thresholds", "sample": 0}
    con = db.connect(cat_db)
    try:
        rows = con.execute("SELECT score, reason FROM viral_labels").fetchall()
    finally:
        con.close()
    assert all(row["score"] is not None for row in rows), "有阅读量就必须出分,不能静默不评分"
    assert all("独立口径" in row["reason"] for row in rows)

    out = viral.calibrate_reads("demo", db_path=cat_db)
    assert out["status"] == "calibrated" and out["sample"] == n
    relabel = viral.label_category("demo", db_path=cat_db)
    assert relabel["calibration_reads"]["status"] == "calibrated"
    assert relabel["calibration"]["status"] == "default_thresholds", "reads 池标定不得写到 measured 池"
    con = db.connect(cat_db)
    try:
        keys = {row[0] for row in con.execute("SELECT key FROM articles_meta WHERE key LIKE 'viral_calibration%'")}
    finally:
        con.close()
    assert keys == {"viral_calibration_reads"}


def test_two_pools_calibrate_independently(cat_db):
    """measured 与 measured_reads 各自拟合、各自落 key、样本数互不串池。"""
    n = viral.MIN_CALIBRATION_SAMPLE + 10
    _seed_articles(cat_db, 2 * n)
    _seed_metrics(cat_db, n)  # 1..n:有曝光量 ⇒ measured
    _seed_reads_metrics(cat_db, range(n + 1, 2 * n + 1))  # 其余:只有阅读量 ⇒ measured_reads
    viral.label_category("demo", db_path=cat_db)

    measured = viral.calibrate("demo", db_path=cat_db)
    assert measured["status"] == "calibrated" and measured["sample"] == n
    con = db.connect(cat_db)
    try:
        keys = {row[0] for row in con.execute("SELECT key FROM articles_meta WHERE key LIKE 'viral_calibration%'")}
        measured_raw = con.execute(
            "SELECT value FROM articles_meta WHERE key='viral_calibration'"
        ).fetchone()[0]
    finally:
        con.close()
    assert keys == {"viral_calibration"}, "measured 标定不得给 reads 池写阈值"

    reads = viral.calibrate_reads("demo", db_path=cat_db)
    assert reads["status"] == "calibrated" and reads["sample"] == n
    con = db.connect(cat_db)
    try:
        stored = {row[0]: json.loads(row[1]) for row in con.execute(
            "SELECT key, value FROM articles_meta WHERE key LIKE 'viral_calibration%'"
        )}
    finally:
        con.close()
    assert set(stored) == {"viral_calibration", "viral_calibration_reads"}
    assert stored["viral_calibration"]["sample"] == n
    assert stored["viral_calibration_reads"]["sample"] == n
    assert json.dumps(stored["viral_calibration"], sort_keys=True) == json.dumps(
        json.loads(measured_raw), sort_keys=True
    ), "reads 标定不得改写 measured 池的阈值"


def test_reads_calibration_refuses_small_sample(cat_db):
    _seed_articles(cat_db, 5)
    _seed_reads_metrics(cat_db, range(1, 6))
    viral.label_category("demo", db_path=cat_db)
    out = viral.calibrate_reads("demo", db_path=cat_db)
    assert out["status"] == "insufficient_sample" and out["sample"] == 5
    assert viral.calibrate("demo", db_path=cat_db)["sample"] == 0, "reads 行不得算进 measured 样本"
    con = db.connect(cat_db)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM articles_meta WHERE key='viral_calibration_reads'"
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_reads_pool_needs_only_reads_and_never_zero_fills(cat_db):
    """T-25:阅读量口径只以 reads 为基准;点赞/评论缺失不参与、不代入 0;reads 缺失才不评分。"""
    _seed_articles(cat_db, 2)
    con = db.connect(cat_db)
    con.execute(
        "INSERT INTO article_metrics(article_id, reads, likes, favorites, comments, impressions,"
        " data_source, credibility, captured_at) VALUES(1, 1000, NULL, NULL, NULL, NULL, 'fixture_src', 'self_reported', '2026-11-13T00:00:00+08:00')"
    )
    con.execute(
        "INSERT INTO article_metrics(article_id, reads, likes, favorites, comments, impressions,"
        " data_source, credibility, captured_at) VALUES(2, NULL, 5, NULL, 1, NULL, 'fixture_src', 'self_reported', '2026-11-13T00:00:00+08:00')"
    )
    con.commit()
    con.close()
    viral.label_category("demo", db_path=cat_db)
    con = db.connect(cat_db)
    try:
        row1 = con.execute(
            "SELECT confidence_tier, score, reason FROM viral_labels WHERE article_id=1"
        ).fetchone()
        row2 = con.execute(
            "SELECT confidence_tier, score FROM viral_labels WHERE article_id=2"
        ).fetchone()
    finally:
        con.close()
    assert row1["confidence_tier"] == viral.TIER_MEASURED_READS
    assert row1["score"] == pytest.approx(0.5, abs=1e-4)
    assert "缺失不代入 0" in row1["reason"]
    assert row2["confidence_tier"] == viral.TIER_NO_IMPRESSIONS and row2["score"] is None


def test_import_gate_rejects_unauthorized_source(cat_db, tmp_path):
    _seed_articles(cat_db, 1)
    fixture = tmp_path / "metrics.csv"
    fixture.write_text("article_id,reads\n1,100\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        metrics_import.import_metrics("demo", fixture, "made_up_api", db_path=cat_db, authorized={})
