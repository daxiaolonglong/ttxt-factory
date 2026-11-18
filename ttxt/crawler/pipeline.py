"""P3 清洗管线:正文清洗 → 时间归一化 → simhash 聚类 → 检索索引(T-17 增量化)。

账本 T-17 实测(2026-11-18 前置):social 40,506 行 `ttxt clean` 47m51.9s、
`ttxt analyze` 的 index 段 236.27s —— 全部来自"每轮从零重算"。现在按
``pipeline_state`` 指纹台账增量:

- 行未变(台账 content_fp 与本轮重算指纹一致)⇒ 跳过 clean_article 与 articles 更新,
  simhash 从台账复用;
- 日期归一化对**跳过行也照样跑**(normalize_datetime 对已是 ISO 的输出不是自等,
  直接跳过会把该归一化的行永久留在旧值),只有值真变才发 UPDATE;
- 聚类走位块分桶(超集保证,精确过滤),FTS 走行id 对齐增量,预算超限自动整库重建;
- ``full=True``(``ttxt clean --full``)强制走全量路径 —— 这是被点名的回退通道。

增量结果与全量结果**必须逐行相等**,回执里带 mode / stage_seconds / 聚类与索引子回执,
对账不过就查得出来。
"""

from __future__ import annotations

import time
from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect
from ttxt.logging import get_logger

from . import cleaner, clusters, simhash, store, timeparse

LOG = get_logger("crawler.pipeline")

CLEAN_VERSION = "p3-v1"


def run_pipeline(category: str, db_path: Path | None = None, full: bool = False) -> dict:
    path = store.migrate_articles_db(category, db_path=db_path)
    settings = cfg.pipeline_settings()
    stage_seconds: dict[str, float] = {}

    started = time.perf_counter()
    changed_ids: list[int] = []
    removed_ids: list[int] = []
    rows_seen = rows_cleaned = rows_reused = dates_normalized = pruned = 0
    con = connect(path)
    try:
        ledger = {
            int(row["article_id"]): str(row["content_fp"])
            for row in con.execute("SELECT article_id, content_fp FROM pipeline_state")
        }
        rows = con.execute("SELECT id, title, content, published_at FROM articles ORDER BY id").fetchall()
        # 台账为空(首轮/被整表清过)⇒ 本轮就是冷启动,如实报 full;之后才谈增量。
        mode = "full" if full or not ledger else "incremental"

        # 孤儿台账行必须裁剪并计数:conftest 裁剪副本、dedupe-history、legacy 都在管线外删文章。
        # 删之前先把"被删的 id"留给 FTS 增量(文章行没了,索引行也必须跟着没)。
        existing_ids = {int(row["id"]) for row in rows}
        removed_ids = [article_id for article_id in ledger if article_id not in existing_ids]
        if ledger:
            pruned = con.execute(
                "DELETE FROM pipeline_state WHERE article_id NOT IN (SELECT id FROM articles)"
            ).rowcount

        now = cfg.now_iso()
        for row in rows:
            article_id = int(row["id"])
            fp = clusters.content_fingerprint(CLEAN_VERSION, row["title"], row["content"])
            normalized_date = timeparse.normalize_datetime(row["published_at"])
            date_changed = normalized_date is not None and normalized_date != row["published_at"]
            if date_changed:
                dates_normalized += 1
            if mode == "incremental" and ledger.get(article_id) == fp:
                rows_reused += 1
                # 跳过≠不纠偏:日期归一化对未变行照样判,值真变了才发 UPDATE(罕见)。
                if date_changed:
                    con.execute("UPDATE articles SET published_at = ? WHERE id = ?", (normalized_date, article_id))
                continue
            cleaned = cleaner.clean_article(f"{row['title'] or ''}\n{row['content'] or ''}")
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
                    article_id,
                ),
            )
            # 台账里的 simhash/text_len 必须与 clusters.text_of 同一口径:
            # cleaned_content 有值用清洗文本,否则回退 f"{title}\n{content}"。
            sim_text = cleaned if cleaned else f"{row['title'] or ''}\n{row['content'] or ''}"
            digest = simhash.to_hex(simhash.simhash64(sim_text))
            con.execute(
                "INSERT OR REPLACE INTO pipeline_state(article_id, content_fp, simhash, text_len, fp_at)"
                " VALUES(?, ?, ?, ?, ?)",
                (article_id, fp, digest, len(sim_text), now),
            )
            changed_ids.append(article_id)
            rows_cleaned += 1
        con.commit()
    finally:
        con.close()
    stage_seconds["clean"] = round(time.perf_counter() - started, 2)

    started = time.perf_counter()
    stats = clusters.cluster_category(
        category,
        db_path=path,
        clean_version=CLEAN_VERSION,
        report_over=int(settings["cluster_bucket_report_over"]),
    )
    stage_seconds["cluster"] = round(time.perf_counter() - started, 2)

    # 检索索引必须在采集当场补上,不能等 `ttxt analyze`。
    # 根因(2026-11-16 生产实测):`articles_fts` 原先只在 `analyze → rag.rebuild_index` 里写,
    # 采集任务只跑本管线 ⇒ `doctor.db.fts_coverage` 抓到 374 行只有 354 行可搜,
    # 当天采回来的文章在 `ttxt search` / 写作取材里看不见。
    # 依赖方向是单向的:`analysis.rag` 只依赖 `crawler.simhash` 的分词,不回头依赖采集层,
    # 所以这里用函数内导入,避免模块级循环导入。
    from ttxt.analysis import rag

    started = time.perf_counter()
    index_stats = rag.sync_index(
        category,
        db_path=path,
        changed_ids=changed_ids,
        removed_ids=removed_ids,
        force_full=full or mode == "full",
    )
    stage_seconds["index"] = round(time.perf_counter() - started, 2)

    stats.update(
        {
            "cleaned": rows_cleaned,
            "rows_seen": len(rows),
            "rows_cleaned": rows_cleaned,
            "rows_reused": rows_reused,
            "fingerprints_changed": len(changed_ids),
            "fingerprints_pruned": pruned,
            "dates_normalized": dates_normalized,
            "clean_version": CLEAN_VERSION,
            "mode": mode,
            "stage_seconds": stage_seconds,
            "indexed": index_stats["indexed"],
            "index": index_stats,
        }
    )
    LOG.info(
        "pipeline.done",
        category=category,
        mode=mode,
        rows_seen=len(rows),
        rows_cleaned=rows_cleaned,
        rows_reused=rows_reused,
        clusters=stats["clusters"],
        near_duplicate_pairs=stats["near_duplicate_pairs"],
        candidate_pairs_examined=stats["candidate_pairs_examined"],
        brute_force_pair_budget=stats["brute_force_pair_budget"],
        index_mode=index_stats["mode"],
        **{f"stage_{name}_s": value for name, value in stage_seconds.items()},
    )
    return stats
