"""关键词:种子维护、效果评分、反哺候选(无采集覆盖的关键词)。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect

DEMO_SEEDS = ["人工智能", "开源", "效率工具", "数据"]

# P26 ⓪(2026-11-16):反哺入队的冷却窗口。实测同一天 ``actor=keyword_feedback`` 入队 4 次
# (job 74/75/76/77),gap 词始终是那两个,health 一支每次 ``inserted 0 / duplicates 25``
# ⇒ gap 补不上却每轮重新排队,白烧限速预算、队列永不清空。
DEFAULT_FEEDBACK_COOLDOWN_HOURS = 24.0


def _cooldown_remaining_hours(last_enqueued_at: str | None, cooldown_hours: float) -> float | None:
    """还需等待的小时数;无需等待(无记录 / 已过期)返回 ``None``。

    ``cooldown_hours <= 0`` ⇒ 关闭冷却(运维逃生阀、测试、紧急补采)。
    时间戳读不出来按"无需等待"处理:宁可多采一次,不可永远不采。
    """
    if cooldown_hours <= 0 or not last_enqueued_at:
        return None
    try:
        then = datetime.fromisoformat(str(last_enqueued_at))
    except ValueError:
        return None
    now = datetime.now(then.tzinfo) if then.tzinfo else datetime.now()
    remaining = cooldown_hours - (now - then).total_seconds() / 3600.0
    return round(remaining, 2) if remaining > 0 else None


def seed_keywords(category: str, db_path: Path | None = None) -> int:
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    seeds: list[str] | None = None
    try:
        for item in cfg.load_registry().get("categories", []):
            if item.get("slug") == category:
                seeds = item.get("seed_keywords")
                break
    except Exception:  # noqa: BLE001 - 注册表缺失时用默认种子
        seeds = None
    if not seeds:
        seeds = DEMO_SEEDS
    now = cfg.now_iso()
    con = connect(target)
    try:
        for keyword in seeds:
            con.execute(
                "INSERT OR IGNORE INTO keywords(keyword, category, origin, status, created_at) VALUES (?, ?, 'seed', 'active', ?)",
                (keyword, category, now),
            )
        con.commit()
        total = int(con.execute("SELECT COUNT(*) FROM keywords").fetchone()[0])
    finally:
        con.close()
    return total


def score_keywords(category: str, db_path: Path | None = None) -> dict:
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    con = connect(target)
    try:
        # 覆盖统计必须覆盖全部关键词(含 gap):若只统计 active,gap 词再也不会被重新计数,
        # auto_feedback 的 gap→active 恢复通道就永远无法触发(闭环断在评分层)。
        rows = con.execute("SELECT id, keyword FROM keywords ORDER BY id").fetchall()
        articles = con.execute("SELECT cleaned_content, content FROM articles").fetchall()
        texts = [(row["cleaned_content"] or row["content"] or "") for row in articles]
        lengths = [len(text) for text in texts]
        now = cfg.now_iso()
        collected_rows: list[tuple[int, str, int, int, float]] = []
        for row in rows:
            keyword = row["keyword"]
            hits = [index for index, text in enumerate(texts) if keyword in text]
            collected = len(hits)
            quality = sum(1 for index in hits if lengths[index] >= 120)
            average_length = (sum(lengths[index] for index in hits) / collected) if collected else 0.0
            collected_rows.append((row["id"], keyword, collected, quality, average_length))
        max_collected = max((item[2] for item in collected_rows), default=0)
        for keyword_id, _keyword, collected, quality, average_length in collected_rows:
            collected_norm = (collected / max_collected) if max_collected else 0.0
            quality_ratio = (quality / collected) if collected else 0.0
            score = round(0.6 * collected_norm + 0.4 * quality_ratio, 4)
            con.execute(
                """
                INSERT INTO keyword_performance(
                  keyword_id, collected, quality_articles, avg_text_length, score, last_used_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(keyword_id) DO UPDATE SET
                  collected = excluded.collected,
                  quality_articles = excluded.quality_articles,
                  avg_text_length = excluded.avg_text_length,
                  score = excluded.score,
                  last_used_at = excluded.last_used_at,
                  updated_at = excluded.updated_at
                """,
                (keyword_id, collected, quality, round(average_length, 1), score, now, now),
            )
        con.commit()
        candidates = [item[1] for item in collected_rows if item[2] == 0]
    finally:
        con.close()
    return {
        "keywords": len(collected_rows),
        "zero_coverage": len(candidates),
        "feedback_candidates": candidates,
    }


def auto_feedback(
    category: str,
    db_path: Path | None = None,
    system_db: Path | None = None,
    sources_path: Path | None = None,
    evidence_dir: Path | None = None,
    cooldown_hours: float = DEFAULT_FEEDBACK_COOLDOWN_HOURS,
) -> dict:
    """B3 关键词反哺闭环:零覆盖关键词 → gap 状态 → 自动入队补充采集(只入队)。

    - 关键词从 gap 恢复覆盖 → 回到 active;
    - 零覆盖 gap 关键词 → 为该类目正式来源入队一条采集任务(已有排队/运行任务则去重);
    - **冷却窗口**:同一来源距上次反哺入队不足 ``cooldown_hours``(默认 24h)不再入队,
      只记录证据;``cooldown_hours=0`` 关闭冷却(紧急补采/测试);
    - 每次动作都落证据文件到 03_intelligence/keyword_feedback/,可审计;
    - 演示类目没有正式来源:只写证据文件,不入队。
    """
    from ttxt.crawler import sources as source_loader
    from ttxt.crawler import store

    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    recovered = 0
    gaps: list[str] = []
    con = connect(target)
    try:
        zero_rows = con.execute(
            "SELECT k.id, k.keyword FROM keywords k"
            " JOIN keyword_performance p ON p.keyword_id = k.id"
            " WHERE k.status='active' AND p.collected = 0"
        ).fetchall()
        for row in zero_rows:
            con.execute("UPDATE keywords SET status='gap' WHERE id=?", (row["id"],))
        recovered = int(
            con.execute(
                "SELECT COUNT(*) FROM keywords k"
                " JOIN keyword_performance p ON p.keyword_id = k.id"
                " WHERE k.status='gap' AND p.collected > 0"
            ).fetchone()[0]
        )
        if recovered:
            con.execute(
                "UPDATE keywords SET status='active' WHERE status='gap' AND id IN"
                " (SELECT p.keyword_id FROM keyword_performance p WHERE p.collected > 0)"
            )
        gaps = [
            row["keyword"]
            for row in con.execute(
                "SELECT keyword FROM keywords WHERE category=? AND status='gap' ORDER BY keyword", (category,)
            ).fetchall()
        ]
        con.commit()
    finally:
        con.close()

    job_id: int | None = None
    job_note = None
    cooldown_remaining: float | None = None
    last_enqueued_at: str | None = None
    feedback_attempts: int | None = None
    if gaps:
        loader_kwargs = {"path": Path(sources_path)} if sources_path else {}
        try:
            candidates_sources = [
                item for item in source_loader.load_sources(**loader_kwargs)
                if item.category == category and item.enabled and not item.demo
            ]
        except FileNotFoundError:
            candidates_sources = []
        if candidates_sources:
            target_source = candidates_sources[0]
            existing = store.find_pending_crawl_job(target_source.id, category, db_path=system_db)
            if existing is not None:
                job_id, job_note = existing, "已有排队/运行任务,本次不重复入队"
            else:
                # 冷却窗口:一次反哺至少要给采集一次机会,否则 gap 词会永远重新排队。
                # 状态落在类目库 articles_meta.dispatch_state,不落在 crawl_jobs ——
                # 任务一跑完 finish_crawl_job 用回执整体覆盖 meta_json,provenance 就没了。
                state = store.load_dispatch_state(target)
                feedback_state = state.get("keyword_feedback")
                if not isinstance(feedback_state, dict):
                    feedback_state = {}
                entry = feedback_state.get(target_source.id)
                entry = entry if isinstance(entry, dict) else {}
                last_enqueued_at = entry.get("last_enqueued_at")
                attempts = int(entry.get("attempts") or 0) + 1
                feedback_attempts = attempts
                cooldown_remaining = _cooldown_remaining_hours(last_enqueued_at, cooldown_hours)
                if cooldown_remaining is not None:
                    # 不入队也要计数:尝试次数是"这个 gap 词采不动"的证据,不是入队的副产品。
                    entry = {**entry, "attempts": attempts}
                    job_note = (
                        f"冷却中:距上次反哺入队还需 {cooldown_remaining:g} 小时"
                        f"(窗口 {cooldown_hours:g} 小时),本次不入队;该来源累计第 {attempts} 次反哺尝试"
                    )
                else:
                    job_id = store.create_crawl_job(
                        target_source.id,
                        category,
                        db_path=system_db,
                        meta={"reason": "keyword_gap_feedback", "keywords": gaps,
                              "dispatched_via": "keyword_feedback", "attempt": attempts},
                    )
                    job_note = "已入队补充采集任务(不自动执行)"
                    last_enqueued_at = cfg.now_iso()
                    entry = {"last_enqueued_at": last_enqueued_at, "attempts": attempts}
                feedback_state[target_source.id] = entry
                state["keyword_feedback"] = feedback_state
                store.save_dispatch_state(state, db_path=target)
        else:
            job_note = "该类目无正式启用来源(或演示类目),仅记录反哺证据"

    evidence = {
        "category": category,
        "generated_at": cfg.now_iso(),
        "gap_keywords": gaps,
        "recovered_keywords": recovered,
        "queued_job_id": job_id,
        "note": job_note,
        "cooldown_hours": cooldown_hours,
        "cooldown_remaining_hours": cooldown_remaining,
        "last_enqueued_at": last_enqueued_at,
        "feedback_attempts": feedback_attempts,
    }
    file_written = None
    if gaps or recovered:
        out_dir = Path(evidence_dir) if evidence_dir else cfg.INTELLIGENCE_DIR / "keyword_feedback"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = cfg.now_iso().replace(":", "").replace("-", "")
        out_path = out_dir / f"{category}-feedback-{stamp}.json"
        out_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        file_written = str(out_path)
    evidence["evidence_file"] = file_written
    return evidence
