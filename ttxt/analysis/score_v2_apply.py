"""R47:把已验证的 score_v2 接进正式评分/预测链路(只增不改)。

接入方式(全部"只增"):
- 新表 ``score_v2_labels``(store.SCORE_V2_SCHEMA):每篇一行的连续分并列读数;
  旧 ``viral_labels`` / ``prediction_records`` 的写入路径**一行不动**,随时可回退;
- ``analyze.run_analysis`` 在 viral 阶段后新增 ``score_v2`` 阶段;模型产物缺失或
  读取失败时本阶段返回 ``status=model_unavailable``,**不影响旧链路与退出码**;
- 特征口径与拟合时完全一致:``score_v2.extract_features(title, content, category)``,
  用 raw ``content``(与 R45 拟合数据源同一列),不引入任何结果类字段;
- 模型只从 ``03_intelligence/score_v2_model.json`` 加载(``score_v2.load_model``
  带版本/特征名/泄漏三重校验,校验失败宁可不打分)。

与门禁的关系:score_v2 分数只做并列参考读数,不喂给任何门禁阈值、取样规则、
A+B 口径与事实可核率口径 —— 那些实现零改动。
"""

from __future__ import annotations

import json
from pathlib import Path

from ttxt import config as cfg
from ttxt.analysis import score_v2
from ttxt.crawler import store
from ttxt.db import connect
from ttxt.logging import get_logger

LOG = get_logger("analysis.score_v2")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_PATH = REPO_ROOT / "03_intelligence" / "score_v2_model.json"


def apply_category(category: str, db_path: Path | None = None, *, model_path: Path | str | None = None) -> dict:
    """对单类目全库刷 score_v2 并列分(幂等全量刷新)。

    fail-open 覆盖两类故障,一律返回 ``status`` 而不抛出,旧链路
    (viral/keywords/knowledge/index)照常完成、退出码不变:
    - 模型缺失 / JSON 损坏 / 校验不过 ⇒ ``status=model_unavailable``;
    - 特征抽取、DB 打开/迁移/读写异常 ⇒ ``status=apply_failed``。
      写入走"先清后插 + 单次 commit"事务:异常时未 commit,连接关闭即回滚,
      ``score_v2_labels`` 保持上一次落库状态,不会留下半成品。
    """
    try:
        path = store.migrate_articles_db(category, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 - fail-open:库打不开/迁移失败同样不击穿主流程
        LOG.warn("score_v2.apply_failed", category=category, stage="migrate", error=str(exc))
        return {"status": "apply_failed", "category": category, "error": str(exc), "applied": 0}
    try:
        model = score_v2.load_model(model_path or DEFAULT_MODEL_PATH)
    except (score_v2.ModelFileError, score_v2.LeakageError) as exc:
        LOG.warn("score_v2.model_unavailable", category=category, error=str(exc))
        return {"status": "model_unavailable", "category": category, "error": str(exc), "applied": 0}

    try:
        con = connect(path)
    except Exception as exc:  # noqa: BLE001 - fail-open:连接失败同样收敛
        LOG.warn("score_v2.apply_failed", category=category, stage="connect", error=str(exc))
        return {"status": "apply_failed", "category": category, "error": str(exc), "applied": 0}
    try:
        rows = list(
            con.execute("SELECT id, title, content, category FROM articles ORDER BY id")
        )
        records = []
        for article_id, title, content, row_category in rows:
            features = score_v2.extract_features(title or "", content or "", row_category or category)
            records.append(
                (
                    int(article_id),
                    round(score_v2.predict(model, features), 6),
                    json.dumps(features, ensure_ascii=False),
                    model.get("version", score_v2.VERSION),
                    cfg.now_iso_ms(),
                )
            )
        # 全量刷新:先清后插,表内永远是"当前库全量文章"的并列读数,无孤儿行。
        con.execute("DELETE FROM score_v2_labels")
        con.executemany(
            "INSERT INTO score_v2_labels(article_id, score_v2, features_json, model_version, applied_at)"
            " VALUES (?,?,?,?,?)",
            records,
        )
        con.commit()
        distinct = con.execute("SELECT COUNT(DISTINCT score_v2) FROM score_v2_labels").fetchone()[0]
        return {
            "status": "ok",
            "category": category,
            "applied": len(records),
            "distinct_score_values": int(distinct),
            "model_version": model.get("version", score_v2.VERSION),
            "n_train_model": model.get("n_train"),
        }
    except Exception as exc:  # noqa: BLE001 - fail-open:DB/特征异常不得击穿主流程
        con.rollback()
        LOG.warn("score_v2.apply_failed", category=category, error=str(exc))
        return {"status": "apply_failed", "category": category, "error": str(exc), "applied": 0}
    finally:
        con.close()


def status(category: str, db_path: Path | None = None) -> dict:
    """读取某类目 score_v2_labels 的落库状态(只读)。"""
    path = store.articles_db_path(category) if db_path is None else db_path
    con = connect(path)
    try:
        try:
            row = con.execute(
                "SELECT COUNT(*), MIN(applied_at), MAX(applied_at),"
                " COUNT(DISTINCT model_version), COUNT(DISTINCT score_v2)"
                " FROM score_v2_labels"
            ).fetchone()
        except Exception:  # noqa: BLE001 - 旧库尚未迁移出新表时按"未落库"处理
            return {"status": "not_migrated", "category": category, "rows": 0}
        return {
            "status": "ok",
            "category": category,
            "rows": int(row[0] or 0),
            "first_applied_at": row[1],
            "last_applied_at": row[2],
            "model_versions": int(row[3] or 0),
            "distinct_score_values": int(row[4] or 0),
        }
    finally:
        con.close()


def scores(db_path: Path | None = None) -> dict[int, float]:
    """article_id → score_v2(供并列对照与回溯读数使用,只读)。"""
    con = connect(db_path)
    try:
        return {
            int(row[0]): float(row[1])
            for row in con.execute("SELECT article_id, score_v2 FROM score_v2_labels")
        }
    finally:
        con.close()
