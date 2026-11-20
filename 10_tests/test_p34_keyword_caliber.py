"""P34 ①:关键词「综合得分」与「爆款 signal」必须是两个能各自解释清楚的口径。

真人验收裁定(2026-11-17)指出:报告里 ``keyword_performance.score`` 0.8093 与
``analyze`` 打印的 signal 0.3642 容易被当成同一个指标。两者确实是两个口径:

    base  = 0.6 * (collected / max_collected) + 0.4 * (quality / collected)
    score = 0.7 * base + 0.3 * signal   (有真实 signal 时),否则 score = base

``signal`` 只是 score 里权重 0.3 的一个加数,既不能替换 score,也不能单独当"这个词的效果"。
本测试锁三件事:
1. 评分结果必须把 base / signal / 爆款项贡献 / 公式 一起交出来,报告里不可能再只留一个孤立数字;
2. 库里的 score 必须严格等于该公式的结果(可复算,不是黑箱);
3. 没有真实 signal 时不得凭空加/减爆款项(缺失 ≠ 0 分)。
"""

from __future__ import annotations

import sqlite3

from ttxt.analysis import keywords as kw
from ttxt.analysis import viral
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem


def _demo_db(tmp_path, bodies: dict[int, str]):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    items = [
        # R57(2026-11-20):计分只认标题命中 ⇒ 假标题带上正文词头(关键词),把假语料补厚,不放宽断言。
        ExtractedItem(title=f"{body[:2]} 第 {aid} 篇标题", content=body, excerpt="", author=None,
                      published_at=None, url=f"https://example.com/a{aid}")
        for aid, body in sorted(bodies.items())
    ]
    store.insert_items("demo", "test", items, db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    return db_path


def _seed(db_path) -> None:
    con = sqlite3.connect(db_path)
    try:
        from ttxt import config as cfg
        now = cfg.now_iso()
        for keyword in ("数据", "开源"):
            con.execute("INSERT INTO keywords(keyword, category, origin, status, created_at) VALUES(?, 'demo', 'seed', 'active', ?)",
                        (keyword, now))
        con.commit()
    finally:
        con.close()


def _article_ids(db_path) -> list[int]:
    con = sqlite3.connect(db_path)
    try:
        return [row[0] for row in con.execute("SELECT id FROM articles ORDER BY id")]
    finally:
        con.close()


def _label(db_path, article_id: int, label: str, score: float) -> None:
    from ttxt import config as cfg
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO viral_labels(article_id, label, confidence_tier, score, reason, labeled_at)"
            " VALUES(?,?,?,?,?,?)",
            (article_id, label, viral.TIER_MEASURED, score, "测试:真实测得", cfg.now_iso()),
        )
        con.commit()
    finally:
        con.close()


def _performance(db_path) -> dict[str, dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return {row["keyword"]: dict(row) for row in con.execute(
            "SELECT k.keyword, p.collected, p.quality_articles, p.score FROM keyword_performance p "
            "JOIN keywords k ON k.id=p.keyword_id")}
    finally:
        con.close()


def test_score_details_expose_both_calibers(tmp_path, monkeypatch):
    monkeypatch.setattr(viral, "MIN_CALIBRATION_SAMPLE", 3)  # 只看公式可见性,样本门槛由 P28 测试另行覆盖
    bodies = {1: "数据" + "正文" * 200, 2: "数据" + "正文" * 200, 3: "数据" + "正文" * 200, 4: "数据" + "正文" * 200}
    db_path = _demo_db(tmp_path, bodies)
    _seed(db_path)
    ids = _article_ids(db_path)
    _label(db_path, ids[0], viral.LABEL_VERIFIED, 0.20)
    _label(db_path, ids[1], viral.LABEL_POTENTIAL, 0.06)
    _label(db_path, ids[2], viral.LABEL_NORMAL, 0.01)

    result = kw.score_keywords("demo", db_path=db_path)
    detail = result["viral"]["details"]["数据"]
    assert detail["signal"] == 0.6, "signal = 真实测得标签均值(1.0+0.6+0.2)/3"
    assert detail["measured_hits"] == 3 and detail["matched_articles"] == 4
    assert detail["base"] == 1.0, "覆盖归一 1.0 + 优质率 1.0 ⇒ base=1.0"
    assert detail["viral_component"] == 0.18, "爆款项贡献 = 0.3 * signal,单独可见"
    assert "0.7" in result["viral"]["formula"] and "0.3" in result["viral"]["formula"]

    stored = _performance(db_path)["数据"]
    expected = round(0.7 * detail["base"] + 0.3 * detail["signal"], 4)
    assert stored["score"] == expected == 0.88, "库里的综合得分必须可按公式复算"
    # 两个口径的差必须能被解释:0.88 - 0.6 = 0.28 = 0.7 * (base - signal)
    assert abs((stored["score"] - detail["signal"]) - 0.7 * (detail["base"] - detail["signal"])) < 1e-9


def test_no_signal_keeps_score_at_base_without_fabrication(tmp_path, monkeypatch):
    monkeypatch.setattr(viral, "MIN_CALIBRATION_SAMPLE", 3)
    bodies = {1: "数据" + "正文" * 200, 2: "数据" + "正文" * 200, 3: "数据" + "正文" * 200, 4: "开源" + "正文" * 200}
    db_path = _demo_db(tmp_path, bodies)
    _seed(db_path)
    ids = _article_ids(db_path)
    for aid in ids[:3]:
        _label(db_path, aid, viral.LABEL_NORMAL, 0.01)

    result = kw.score_keywords("demo", db_path=db_path)
    detail = result["viral"]["details"]["开源"]
    assert detail["signal"] is None and detail["measured_hits"] == 0
    assert detail["reason"], "没有真实样本必须写原因,不能留一个 0 分"
    stored = _performance(db_path)["开源"]
    assert stored["score"] == round(detail["base"], 4), "无 signal ⇒ score 就是 base,不掺任何爆款项"
