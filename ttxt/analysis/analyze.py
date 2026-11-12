"""P4 分析编排:迁移 → 关键词 → 爆款标签 → 知识资产 → RAG 索引。"""

from __future__ import annotations

from pathlib import Path

from ttxt.crawler import store

from . import keywords, knowledge, rag, viral


def run_analysis(
    category: str,
    db_path: Path | None = None,
    system_db: Path | None = None,
    sources_path: Path | None = None,
    evidence_dir: Path | None = None,
) -> dict:
    path = store.migrate_articles_db(category, db_path=db_path)
    seed_total = keywords.seed_keywords(category, db_path=path)
    keyword_stats = keywords.score_keywords(category, db_path=path)
    feedback_stats = keywords.auto_feedback(
        category,
        db_path=path,
        system_db=system_db,
        sources_path=sources_path,
        evidence_dir=evidence_dir,
    )
    viral_stats = viral.label_category(category, db_path=path)
    knowledge_stats = knowledge.build_assets(category, db_path=path)
    index_stats = rag.rebuild_index(category, db_path=path)
    return {
        "category": category,
        "seeded_keywords": seed_total,
        "keywords": keyword_stats,
        "feedback": feedback_stats,
        "viral": viral_stats,
        "knowledge": knowledge_stats,
        "index": index_stats,
    }
