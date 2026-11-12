"""P12 批次5:爆款校准诚实口径测试(样本不足→不改口径;样本足→分位拟合)。

注意:本文件中的指标全部是**合成测试夹具**(fixture),仅验证机制,
不构成任何真实爆款结论;真实库中无授权指标时一律 unscored/missing_data。
"""

from __future__ import annotations

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


def test_import_gate_rejects_unauthorized_source(cat_db, tmp_path):
    _seed_articles(cat_db, 1)
    fixture = tmp_path / "metrics.csv"
    fixture.write_text("article_id,reads\n1,100\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        metrics_import.import_metrics("demo", fixture, "made_up_api", db_path=cat_db, authorized={})
