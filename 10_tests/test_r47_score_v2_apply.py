"""R47:score_v2 接入正式评分链路的红线(只增不改、可回退、fail-open)。

红线:
- 旧 ``viral_labels`` 在 apply 前后逐行不变(并列读数,不是替换);
- 模型产物缺失/损坏/校验不过 ⇒ apply 返回 status,不抛异常、不动任何旧表;
- ``load_model`` 对版本、特征名清单、结果类字段三重校验,不合格宁可不打分;
- 新表 ``score_v2_labels`` 由迁移幂等创建;apply 全量刷新、无孤儿行。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ttxt.analysis import analyze, score_v2, score_v2_apply
from ttxt.crawler import store


def _fake_model() -> dict:
    coefficients = {name: 0.0 for name in score_v2.FEATURE_NAMES}
    coefficients["has_quote"] = 0.5
    coefficients["has_digit"] = 0.2
    return {
        "version": score_v2.VERSION,
        "method": "quantile_regression_irls",
        "tau": 0.5,
        "n_train": 250,
        "feature_names": list(score_v2.FEATURE_NAMES),
        "category_reference": score_v2.CATEGORY_REFERENCE,
        "coefficients_raw": coefficients,
        "intercept_raw": -1.0,
    }


def _make_db(tmp_path: Path, category: str = "social", n: int = 6) -> Path:
    db_path = tmp_path / f"{category}.db"
    store.migrate_articles_db(category, db_path=db_path)
    now = "2026-11-19T10:00:00+08:00"
    con = sqlite3.connect(db_path)
    try:
        for index in range(n):
            title = f"标题{index}" + ("「引号」" if index % 2 else "") + (f"{index}个数字" if index % 3 == 0 else "")
            con.execute(
                "INSERT INTO articles(category, source, title, content, url, url_normalized,"
                " content_hash, fetched_at) VALUES(?, 'src', ?, ?, ?, ?, ?, ?)",
                (category, title, f"正文{index}第一段。\n第二段。", f"https://x.test/{index}",
                 f"https://x.test/{index}", f"h{category}{index}", now),
            )
        # 预置旧标签:apply 绝不能碰
        for index in range(1, n + 1):
            con.execute(
                "INSERT INTO viral_labels(article_id, label, confidence_tier, score, labeled_at)"
                " VALUES(?, 'normal', 'measured', 0.5, ?)",
                (index, now),
            )
        con.commit()
    finally:
        con.close()
    return db_path


def _dump_labels(db_path: Path, table: str) -> list[tuple]:
    con = sqlite3.connect(db_path)
    try:
        return [tuple(row) for row in con.execute(f"SELECT * FROM {table} ORDER BY 1")]
    finally:
        con.close()


def test_save_load_roundtrip(tmp_path: Path):
    model = _fake_model()
    path = score_v2.save_model(model, tmp_path / "m.json", meta={"fit_source": "test"})
    loaded = score_v2.load_model(path)
    assert loaded == model


def test_load_model_rejects_bad_artifacts(tmp_path: Path):
    good = _fake_model()

    def write(payload, name):
        path = tmp_path / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    bad_version = dict(good, version="score_v1")
    with pytest.raises(score_v2.ModelFileError):
        score_v2.load_model(write(bad_version, "v.json"))

    bad_names = dict(good, feature_names=["has_digit"])
    with pytest.raises(score_v2.ModelFileError):
        score_v2.load_model(write(bad_names, "n.json"))

    leaked = dict(good, coefficients_raw={**good["coefficients_raw"], "reads": 0.3})
    with pytest.raises(score_v2.LeakageError):
        score_v2.load_model(write(leaked, "l.json"))

    with pytest.raises(score_v2.ModelFileError):
        score_v2.load_model(tmp_path / "missing.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(score_v2.ModelFileError):
        score_v2.load_model(broken)

    with pytest.raises(score_v2.ModelFileError):
        score_v2.save_model(bad_version, tmp_path / "x.json")


def test_migration_creates_new_table_idempotently(tmp_path: Path):
    db_path = tmp_path / "m.db"
    store.migrate_articles_db("social", db_path=db_path)
    store.migrate_articles_db("social", db_path=db_path)
    con = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "score_v2_labels" in tables
    finally:
        con.close()


def test_apply_populates_new_table_and_never_touches_old_labels(tmp_path: Path):
    db_path = _make_db(tmp_path)
    model_path = score_v2.save_model(_fake_model(), tmp_path / "model.json")
    before_viral = _dump_labels(db_path, "viral_labels")

    report = score_v2_apply.apply_category("social", db_path=db_path, model_path=model_path)
    assert report["status"] == "ok"
    assert report["applied"] == 6
    assert report["distinct_score_values"] >= 2, "连续分必须把文本差异体现出来"

    con = sqlite3.connect(db_path)
    try:
        rows = list(con.execute(
            "SELECT article_id, score_v2, features_json, model_version FROM score_v2_labels ORDER BY article_id"
        ))
    finally:
        con.close()
    assert [row[0] for row in rows] == list(range(1, 7))
    assert all(row[3] == score_v2.VERSION for row in rows)
    # 引号特征确实进了 features_json
    features_2 = json.loads(rows[1][2])
    assert set(features_2) == set(score_v2.FEATURE_NAMES)
    assert not (set(features_2) & score_v2.OUTCOME_FIELDS)

    # 旧表逐行不变 —— 并列读数,不是替换
    assert _dump_labels(db_path, "viral_labels") == before_viral

    # 幂等:再刷一次,行数与 article_id 集合不变、无孤儿行
    score_v2_apply.apply_category("social", db_path=db_path, model_path=model_path)
    second = _dump_labels(db_path, "score_v2_labels")
    assert [row[0] for row in second] == list(range(1, 7))


def test_apply_fail_open_when_model_unavailable(tmp_path: Path):
    db_path = _make_db(tmp_path)
    before_viral = _dump_labels(db_path, "viral_labels")
    report = score_v2_apply.apply_category("social", db_path=db_path, model_path=tmp_path / "nope.json")
    assert report["status"] == "model_unavailable"
    assert report["applied"] == 0
    # 不抛异常、不写新表、旧表原样
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM score_v2_labels").fetchone()[0] == 0
    finally:
        con.close()
    assert _dump_labels(db_path, "viral_labels") == before_viral


def test_status_reads_back_and_handles_empty(tmp_path: Path):
    db_path = _make_db(tmp_path)
    empty_status = score_v2_apply.status("social", db_path=db_path)
    assert empty_status["status"] == "ok" and empty_status["rows"] == 0

    model_path = score_v2.save_model(_fake_model(), tmp_path / "model.json")
    score_v2_apply.apply_category("social", db_path=db_path, model_path=model_path)
    after = score_v2_apply.status("social", db_path=db_path)
    assert after["rows"] == 6 and after["distinct_score_values"] >= 2


def test_apply_fail_open_when_db_write_broken(tmp_path: Path):
    """DB 写入异常同样被收敛为 status=apply_failed,不击穿主流程、旧表原样。"""
    db_path = _make_db(tmp_path)
    model_path = score_v2.save_model(_fake_model(), tmp_path / "model.json")
    before_viral = _dump_labels(db_path, "viral_labels")

    # 注入 DB 写入故障:把新表换成结构不匹配的坏表,INSERT 必失败(不动 C 扩展类型)
    con = sqlite3.connect(db_path)
    try:
        con.execute("DROP TABLE score_v2_labels")
        con.execute("CREATE TABLE score_v2_labels(bad_col TEXT)")
        con.commit()
    finally:
        con.close()

    report = score_v2_apply.apply_category("social", db_path=db_path, model_path=model_path)
    assert report["status"] == "apply_failed"
    assert report["applied"] == 0
    # DELETE 随事务回滚,写入半途而废不留新行;旧表逐行不变
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM score_v2_labels").fetchone()[0] == 0
    finally:
        con.close()
    assert _dump_labels(db_path, "viral_labels") == before_viral


def test_apply_fail_open_when_db_unopenable(tmp_path: Path):
    """库文件本身不是 SQLite(打不开/迁移失败)同样收敛为 apply_failed,不抛出。"""
    bogus = tmp_path / "not_a.db"
    bogus.write_text("this is not a sqlite database", encoding="utf-8")
    model_path = score_v2.save_model(_fake_model(), tmp_path / "model.json")
    report = score_v2_apply.apply_category("social", db_path=bogus, model_path=model_path)
    assert report["status"] == "apply_failed"
    assert report["applied"] == 0


def test_run_analysis_runs_score_v2_stage_after_viral(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """行为断言:真实跑 run_analysis,stages 里必须有 score_v2、位于 viral 之后,
    且旧 viral 阶段照常执行(护栏:接入是"只增",不是改写旧链路)。"""
    order: list[str] = []

    def fake_apply(category: str, db_path=None, *, model_path=None) -> dict:
        order.append("score_v2")
        return {"status": "ok", "category": category, "applied": 0}

    monkeypatch.setattr(analyze.score_v2_apply, "apply_category", fake_apply)
    db_path = _make_db(tmp_path, category="demo")
    from ttxt import db as ttxt_db

    ttxt_db.init_system_db(tmp_path / "sys.db")
    result = analyze.run_analysis(
        "demo",
        db_path=db_path,
        system_db=tmp_path / "sys.db",
        evidence_dir=tmp_path / "feedback",
    )
    stages = list(result["stage_seconds"])
    assert "viral" in stages and "score_v2" in stages
    assert order == ["score_v2"], "score_v2 阶段必须真的被调用"
    assert stages.index("viral") < stages.index("score_v2")
    assert result["score_v2"]["status"] == "ok"
    # 旧 viral 阶段照常产出(护栏:只增,不改写旧链路)
    assert result["viral"]["tiers"] == {"missing_data": 6}
