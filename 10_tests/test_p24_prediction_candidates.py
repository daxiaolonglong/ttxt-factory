"""P24 预测闭环取候选的根因测试(先红后绿)。

生产实况:采集入口每次都会跑 analyze/relabel,于是**每一篇**文章都有 `viral_labels` 行
(没有指标的标 `missing_data/unscored`)。而 `prediction.predict_articles` 旧代码的候选条件是
``WHERE v.article_id IS NULL``(只找**完全没有标签行**的文章)⇒ 生产库里永远挑不出候选,
`prediction_records` 恒为 0,闭环空转;并且 ``LIMIT`` 在"是否已有预测"过滤之前生效,
即使有候选也会每轮重复挑同一批(与 P22 指标候选轮转同一类根因)。

断言口径:
- 已有标签但**未实测**的文章必须能被预测;
- 已实测(measured)的文章不许预测(它已经有真值,预测它是自欺);
- 带 ``limit`` 连续跑要往前推进,不许原地打转;
- 预测之后才拿到实测指标的文章,``settle`` 要回填真实分值,其余保持 pending。
"""

from __future__ import annotations

import sqlite3

from ttxt.analysis import metrics_import, prediction, viral
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem

CATEGORY = "demo"


def _item(title: str, content: str, url: str) -> ExtractedItem:
    return ExtractedItem(title=title, content=content, excerpt="", author="", published_at=None, url=url)


def _write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _subjects(db_path) -> list[str]:
    con = sqlite3.connect(db_path)
    try:
        return [row[0] for row in con.execute("SELECT subject FROM prediction_records ORDER BY subject")]
    finally:
        con.close()


def _labeled_state(db_path) -> dict[str, str]:
    con = sqlite3.connect(db_path)
    try:
        return {
            f"article:{row[0]}": row[1]
            for row in con.execute("SELECT article_id, confidence_tier FROM viral_labels ORDER BY article_id")
        }
    finally:
        con.close()


def _production_shaped_db(tmp_path, total: int = 5, measured: int = 3):
    """先把所有文章入库,再统一 analyze 打标签 —— 生产里就是这个顺序。"""
    db_path = store.init_articles_db(CATEGORY, db_path=tmp_path / "demo.db")
    items = [
        _item(f"开源效率工具哪个好{i}?", f"开源效率工具与人工智能的深度评测内容,数据分析和表格处理。第 {i} 篇。" * 12,
              f"https://example.com/p24/{i}")
        for i in range(1, total + 1)
    ]
    store.insert_items(CATEGORY, "unit_test", items, db_path=db_path)
    store.migrate_articles_db(CATEGORY, db_path=db_path)
    lines = ["article_id,reads,likes,favorites,comments,impressions"]
    for article_id in range(1, measured + 1):
        lines.append(f"{article_id},{5000 - article_id * 700},{600 - article_id * 90},{400 - article_id * 60},{80 - article_id * 10},{10000 - article_id * 900}")
    csv_path = _write(tmp_path, "measured.csv", "\n".join(lines) + "\n")
    metrics_import.import_metrics(CATEGORY, csv_path, "unit_test_source", db_path=db_path,
                                  authorized={"unit_test_source": "verified"})
    viral.label_category(CATEGORY, db_path=db_path)   # 关键:全部文章都有标签行
    return db_path


def test_predict_covers_unmeasured_articles_that_already_have_labels(tmp_path):
    db_path = _production_shaped_db(tmp_path, total=5, measured=3)
    state = _labeled_state(db_path)
    assert list(state.values()).count("measured") == 3
    assert list(state.values()).count("missing_data") == 2, "没指标的行必须已被打标签(missing_data),不是没标签"

    result = prediction.predict_articles(CATEGORY, db_path=db_path)
    assert result["recorded"] == 2, (
        "已有 missing_data 标签的文章也必须进入预测候选;旧口径只找无标签行 ⇒ 生产里恒为 0"
    )
    assert sorted(_subjects(db_path)) == ["article:4", "article:5"]


def test_predict_never_predictions_articles_that_already_have_ground_truth(tmp_path):
    db_path = _production_shaped_db(tmp_path, total=5, measured=3)
    prediction.predict_articles(CATEGORY, db_path=db_path)
    subjects = set(_subjects(db_path))
    assert {"article:1", "article:2", "article:3"}.isdisjoint(subjects), "已实测的文章不许再预测(已有真值)"


def test_predict_with_limit_makes_progress_across_rounds(tmp_path):
    db_path = _production_shaped_db(tmp_path, total=6, measured=3)
    first = prediction.predict_articles(CATEGORY, db_path=db_path, limit=1)
    second = prediction.predict_articles(CATEGORY, db_path=db_path, limit=1)
    third = prediction.predict_articles(CATEGORY, db_path=db_path, limit=1)
    assert (first["recorded"], second["recorded"], third["recorded"]) == (1, 1, 1), (
        f"带 limit 连跑必须往前推进,不得原地挑同一批:三次结果 {[first['recorded'], second['recorded'], third['recorded']]}"
    )
    assert len(set(_subjects(db_path))) == 3


def test_settle_backfills_once_real_metrics_arrive(tmp_path):
    db_path = _production_shaped_db(tmp_path, total=5, measured=3)
    first = prediction.predict_articles(CATEGORY, db_path=db_path)
    assert first["recorded"] == 2
    pending = prediction.settle_predictions(CATEGORY, db_path=db_path)
    assert pending["settled"] == 0 and pending["pending"] == 2, "还没有实测值 ⇒ 不许回填"

    csv_path = _write(tmp_path, "later.csv", "article_id,reads,likes,favorites,comments,impressions\n"
                                             "4,4200,520,300,70,9200\n")
    metrics_import.import_metrics(CATEGORY, csv_path, "unit_test_source", db_path=db_path,
                                  authorized={"unit_test_source": "verified"})
    viral.label_category(CATEGORY, db_path=db_path)
    settled = prediction.settle_predictions(CATEGORY, db_path=db_path)
    assert settled["settled"] == 1 and settled["pending"] == 1

    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT outcome_score, outcome_at, notes FROM prediction_records WHERE subject='article:4'"
        ).fetchone()
    finally:
        con.close()
    assert row is not None and row[0] is not None and row[1] and "实测回填" in (row[2] or "")


def test_prediction_report_counts_the_settled_pair(tmp_path):
    db_path = _production_shaped_db(tmp_path, total=5, measured=3)
    prediction.predict_articles(CATEGORY, db_path=db_path)
    csv_path = _write(tmp_path, "later.csv", "article_id,reads,likes,favorites,comments,impressions\n"
                                             "4,4200,520,300,70,9200\n5,1200,90,50,12,7100\n")
    metrics_import.import_metrics(CATEGORY, csv_path, "unit_test_source", db_path=db_path,
                                  authorized={"unit_test_source": "verified"})
    viral.label_category(CATEGORY, db_path=db_path)
    settled = prediction.settle_predictions(CATEGORY, db_path=db_path)
    assert settled["settled"] == 2
    report = prediction.prediction_report(CATEGORY, db_path=db_path, min_samples=2)
    assert report["settled_samples"] == 2
    assert report["mae"] is not None
