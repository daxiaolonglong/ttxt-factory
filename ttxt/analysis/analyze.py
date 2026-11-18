"""P4 分析编排:迁移 → 关键词 → 爆款标签 → 知识资产 → RAG 索引。"""

from __future__ import annotations

import time
from pathlib import Path

from ttxt.crawler import store

from . import keywords, knowledge, rag, viral
from ttxt.logging import get_logger

LOG = get_logger("analysis")


def _timed(stages: dict[str, float], name: str, fn, *args):
    """Record per-stage wall time: the T-17 baseline needs to show where the cost is."""
    started = time.monotonic()
    outcome = fn(*args)
    stages[name] = round(time.monotonic() - started, 2)
    return outcome


def run_analysis(
    category: str,
    db_path: Path | None = None,
    system_db: Path | None = None,
    sources_path: Path | None = None,
    evidence_dir: Path | None = None,
) -> dict:
    path = store.migrate_articles_db(category, db_path=db_path)
    stages: dict[str, float] = {}
    seed_total = _timed(stages, "seed", keywords.seed_keywords, category, path)
    keyword_stats = _timed(stages, "score_keywords", keywords.score_keywords, category, path)
    feedback_stats = _timed(
        stages,
        "auto_feedback",
        lambda: keywords.auto_feedback(
            category,
            db_path=path,
            system_db=system_db,
            sources_path=sources_path,
            evidence_dir=evidence_dir,
        ),
    )
    viral_stats = _timed(stages, "viral", viral.label_category, category, path)
    knowledge_stats = _timed(stages, "knowledge", knowledge.build_assets, category, path)
    index_stats = _timed(stages, "index", lambda: rag.rebuild_index(category, db_path=path))
    LOG.info("analysis.stage.done", category=category, **stages)
    return {
        "category": category,
        "seeded_keywords": seed_total,
        "keywords": keyword_stats,
        "feedback": feedback_stats,
        "viral": viral_stats,
        "knowledge": knowledge_stats,
        "index": index_stats,
        "stage_seconds": stages,
    }
