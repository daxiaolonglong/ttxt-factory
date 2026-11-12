"""关键词:种子维护、效果评分、反哺候选(无采集覆盖的关键词)。"""

from __future__ import annotations

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
        rows = con.execute("SELECT id, keyword FROM keywords WHERE status='active' ORDER BY id").fetchall()
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
