"""关键词:种子维护、效果评分、反哺候选(无采集覆盖的关键词)。"""

from __future__ import annotations

import json
from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect

DEMO_SEEDS = ["人工智能", "开源", "效率工具", "数据"]


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
) -> dict:
    """B3 关键词反哺闭环:零覆盖关键词 → gap 状态 → 自动入队补充采集(只入队)。

    - 关键词从 gap 恢复覆盖 → 回到 active;
    - 零覆盖 gap 关键词 → 为该类目正式来源入队一条采集任务(已有排队/运行任务则去重);
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
                job_id = store.create_crawl_job(
                    target_source.id,
                    category,
                    db_path=system_db,
                    meta={"reason": "keyword_gap_feedback", "keywords": gaps, "dispatched_via": "keyword_feedback"},
                )
                job_note = "已入队补充采集任务(不自动执行)"
        else:
            job_note = "该类目无正式启用来源(或演示类目),仅记录反哺证据"

    evidence = {
        "category": category,
        "generated_at": cfg.now_iso(),
        "gap_keywords": gaps,
        "recovered_keywords": recovered,
        "queued_job_id": job_id,
        "note": job_note,
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
