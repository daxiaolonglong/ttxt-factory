"""P3 清洗管线:正文清洗 → 时间归一化 → simhash 聚类。"""

from __future__ import annotations

from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect

from . import cleaner, clusters, store, timeparse

CLEAN_VERSION = "p3-v1"


def run_pipeline(category: str, db_path: Path | None = None) -> dict[str, int]:
    path = store.migrate_articles_db(category, db_path=db_path)
    con = connect(path)
    try:
        rows = con.execute("SELECT id, title, content, published_at FROM articles ORDER BY id").fetchall()
        now = cfg.now_iso()
        dates_normalized = 0
        for row in rows:
            cleaned = cleaner.clean_article(f"{row['title'] or ''}\n{row['content'] or ''}")
            normalized_date = timeparse.normalize_datetime(row["published_at"])
            if normalized_date and normalized_date != row["published_at"]:
                dates_normalized += 1
            con.execute(
                """
                UPDATE articles
                SET cleaned_content = ?, text_length = ?, cleaned_at = ?, clean_version = ?,
                    published_at = COALESCE(?, published_at)
                WHERE id = ?
                """,
                (
                    cleaned,
                    cleaner.text_length(cleaned),
                    now,
                    CLEAN_VERSION,
                    normalized_date,
                    row["id"],
                ),
            )
        con.commit()
    finally:
        con.close()

    stats = clusters.cluster_category(category, db_path=path)
    stats.update(
        {
            "cleaned": len(rows),
            "dates_normalized": dates_normalized,
            "clean_version": CLEAN_VERSION,
        }
    )
    return stats
