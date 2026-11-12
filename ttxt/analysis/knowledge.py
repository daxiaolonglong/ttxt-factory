"""知识资产:把真实数据统计沉淀为可追溯记录(非模型臆测)。"""

from __future__ import annotations

import json
from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect


def build_assets(category: str, db_path: Path | None = None) -> dict:
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    con = connect(target)
    try:
        total = int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        average_length = float(con.execute("SELECT AVG(text_length) FROM articles").fetchone()[0] or 0.0)
        source_rows = con.execute("SELECT source, COUNT(*) AS n FROM articles GROUP BY source").fetchall()
        source_distribution = {row["source"]: row["n"] for row in source_rows}
        clusters = int(con.execute("SELECT COUNT(*) FROM article_clusters").fetchone()[0])
        keyword_total = int(con.execute("SELECT COUNT(*) FROM keywords").fetchone()[0])
        zero_coverage = int(
            con.execute("SELECT COUNT(*) FROM keyword_performance WHERE collected = 0").fetchone()[0]
        )
        now = cfg.now_iso()
        con.execute("DELETE FROM knowledge_assets WHERE category = ? AND kind IN ('corpus_stats', 'keyword_gap')", (category,))
        evidence = {
            "articles": total,
            "avg_text_length": round(average_length, 1),
            "sources": source_distribution,
            "clusters": clusters,
            "keywords": keyword_total,
        }
        con.execute(
            """
            INSERT INTO knowledge_assets(category, kind, statement, evidence_json, support_count, confidence, updated_at)
            VALUES (?, 'corpus_stats', ?, ?, ?, 'measured', ?)
            """,
            (
                category,
                f"{category} 类目当前语料 {total} 篇,平均正文 {average_length:.0f} 字,来源 {len(source_rows)} 个,相似聚类 {clusters} 个,关键词 {keyword_total} 个。",
                json.dumps(evidence, ensure_ascii=False),
                total,
                now,
            ),
        )
        if zero_coverage:
            con.execute(
                """
                INSERT INTO knowledge_assets(category, kind, statement, evidence_json, support_count, confidence, updated_at)
                VALUES (?, 'keyword_gap', ?, ?, ?, 'measured', ?)
                """,
                (
                    category,
                    f"有 {zero_coverage} 个关键词暂无对应语料,应加入下一轮采集候选。",
                    json.dumps({"zero_coverage_keywords": zero_coverage}, ensure_ascii=False),
                    zero_coverage,
                    now,
                ),
            )
        con.commit()
        asset_count = int(
            con.execute("SELECT COUNT(*) FROM knowledge_assets WHERE category = ?", (category,)).fetchone()[0]
        )
    finally:
        con.close()
    return {"assets": asset_count, "articles": total, "zero_coverage": zero_coverage}
