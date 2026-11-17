"""关键词:种子维护、效果评分、反哺候选(无采集覆盖的关键词)。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ttxt import config as cfg
from ttxt.analysis import viral
from ttxt.db import connect

DEMO_SEEDS = ["人工智能", "开源", "效率工具", "数据"]

# P28(2026-11-16):真实爆款表现要喂回关键词评分。实测缺口:``score_keywords()`` 只看
# "覆盖了几篇文章 + 正文质量",完全不读 ``viral_labels`` ⇒ 同一个词采到 4 篇验证爆款
# 和采到 4 篇低质文章,评分一模一样(红证据 07_tmp/p28_viral_signal/red_evidence.txt)。
# 口径(与 viral.py 同一套词表,不另建一套):
#   1) 只认 ``confidence_tier='measured'`` 且 ``score`` 非空 的真实测得样本;
#      unscored / missing_data / incomplete_data / no_impressions 不进分母,更不当 0 分(规则 6);
#   2) 类目内真实测得样本 < viral.MIN_CALIBRATION_SAMPLE(30)⇒ 标签本身是设计假定阈值,
#      不宣称已标定,爆款项不参与评分(只如实记录状态);
#   3) 单个关键词命中的真实测得文章 < MIN_KEYWORD_MEASURED_HITS ⇒ 词级样本不足,不加权;
#   4) 爆款项最多占 VIRAL_WEIGHT(30%),覆盖与正文质量仍是主干;gap 判定只看 collected,不受影响。
VIRAL_WEIGHT = 0.30
MIN_KEYWORD_MEASURED_HITS = 3
LABEL_VALUE = {
    viral.LABEL_VERIFIED: 1.0,
    viral.LABEL_POTENTIAL: 0.6,
    viral.LABEL_NORMAL: 0.2,
    viral.LABEL_LOW: 0.0,
}
VIRAL_STATUS_APPLIED = "applied"
VIRAL_STATUS_INSUFFICIENT = "insufficient_sample"
VIRAL_STATUS_NO_HITS = "no_measured_hits"

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


def _measured_label_map(con) -> dict[int, str]:
    """本类目库里**真实测得**(tier=measured 且 score 非空)的爆款标签,article_id -> label。

    其余一律不进样本:unscored / missing_data / incomplete_data / no_impressions 没有真实表现数据,
    把它们当 0 分等于凭空给关键词扣钱(规则 6:缺失 ≠ 0)。
    """
    rows = con.execute(
        "SELECT article_id, label FROM viral_labels WHERE confidence_tier=? AND score IS NOT NULL",
        (viral.TIER_MEASURED,),
    ).fetchall()
    return {int(row["article_id"]): row["label"] for row in rows}


def _keyword_signal(article_ids: list[int], measured: dict[int, str]) -> tuple[float | None, int, str]:
    """一个关键词的真实爆款信号 = 命中文章里真实测得样本的标签均值。

    标签取值:验证爆款 1.0 / 潜力 0.6 / 普通 0.2 / 低质 0.0(真低质的词被真实地往下拉)。
    返回 ``(signal, measured_hits, reason)``;``signal is None`` ⇒ 这个词不加爆款项
    (既不虚构加分,也不无据扣分)。
    """
    hits = [article_id for article_id in article_ids if article_id in measured]
    if not hits:
        return None, 0, "命中的文章里没有真实测得表现的数据(缺失不等于 0 分)"
    if len(hits) < MIN_KEYWORD_MEASURED_HITS:
        return None, len(hits), (
            f"命中的真实测得文章 {len(hits)} 篇 < {MIN_KEYWORD_MEASURED_HITS} 篇,词级样本不足")
    values = [LABEL_VALUE[label] for label in (measured[article_id] for article_id in hits)
              if label in LABEL_VALUE]
    if not values:
        return None, len(hits), "命中的真实测得标签不在已知词表内,不猜"
    return round(sum(values) / len(values), 4), len(hits), ""


def score_keywords(category: str, db_path: Path | None = None) -> dict:
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    con = connect(target)
    try:
        # 覆盖统计必须覆盖全部关键词(含 gap):若只统计 active,gap 词再也不会被重新计数,
        # auto_feedback 的 gap→active 恢复通道就永远无法触发(闭环断在评分层)。
        rows = con.execute("SELECT id, keyword FROM keywords ORDER BY id").fetchall()
        # P31:匹配口径同时看「标题 + 正文」。实测现网 532/532 条文章的标题都原样出现在清洗正文里,
        # 所以这次改动对当下覆盖数是 0 影响;它防的是将来某个清洗器把标题从正文里剥掉之后
        # 覆盖统计悄悄掉一半(那种掉法是静默的,和调度停摆同一类问题)。
        articles = con.execute("SELECT id, title, cleaned_content, content FROM articles ORDER BY id").fetchall()
        article_ids = [int(row["id"]) for row in articles]
        bodies = [(row["cleaned_content"] or row["content"] or "") for row in articles]
        titles = [(row["title"] or "") for row in articles]
        # 正文质量与平均长度仍然只看正文:标题命中但正文为空的文章不能算"有效正文"。
        lengths = [len(body) for body in bodies]
        # P28:真实爆款表现喂回评分。只读本类目库的 viral_labels,严禁跨类目。
        measured = _measured_label_map(con)
        calibration = viral.load_calibration(con)
        calibration_state = "calibrated" if calibration else "default_thresholds"
        category_ready = len(measured) >= viral.MIN_CALIBRATION_SAMPLE
        now = cfg.now_iso()
        collected_rows: list[tuple[int, str, int, int, float, float | None]] = []
        signal_details: dict[str, dict] = {}
        for row in rows:
            keyword = row["keyword"]
            if not keyword:
                # 空关键词会让 `keyword in text` 命中全库,把覆盖数与评分整体污染 ⇒ 一律按零覆盖处理
                hits = []
            else:
                hits = [index for index in range(len(article_ids))
                        if keyword in bodies[index] or keyword in titles[index]]
            collected = len(hits)
            quality = sum(1 for index in hits if lengths[index] >= 120)
            average_length = (sum(lengths[index] for index in hits) / collected) if collected else 0.0
            signal, measured_hits, reason = _keyword_signal([article_ids[index] for index in hits], measured)
            if signal is not None and not category_ready:
                signal = None
                reason = (f"类目真实测得样本 {len(measured)} 条 < {viral.MIN_CALIBRATION_SAMPLE} 条,"
                          "阈值仍是设计假定,不宣称已标定,爆款项不参与评分")
            signal_details[keyword] = {
                "signal": signal,
                "measured_hits": measured_hits,
                "matched_articles": collected,
                "reason": reason,
            }
            collected_rows.append((row["id"], keyword, collected, quality, average_length, signal))
        max_collected = max((item[2] for item in collected_rows), default=0)
        for keyword_id, _keyword, collected, quality, average_length, signal in collected_rows:
            collected_norm = (collected / max_collected) if max_collected else 0.0
            quality_ratio = (quality / collected) if collected else 0.0
            base = 0.6 * collected_norm + 0.4 * quality_ratio
            # 有真实爆款信号才混合(最多占 VIRAL_WEIGHT);没有就原样保留:不虚构加分,也不因缺数据扣分
            score = (round((1.0 - VIRAL_WEIGHT) * base + VIRAL_WEIGHT * signal, 4)
                     if signal is not None else round(base, 4))
            # 真人验收裁定(2026-11-17)第 1 条:库里存的 score(如 0.8093)与 analyze 打印的
            # signal(如 0.3642)是**两个口径**,报告里只留一个孤立数字就会被当成同一个指标。
            # 这里把拆项一起交出来,任何一处引用都能按公式复算,不改公式、不改权重。
            detail = signal_details.get(_keyword)
            if detail is not None:
                detail["base"] = round(base, 4)
                detail["collected_norm"] = round(collected_norm, 4)
                detail["quality_ratio"] = round(quality_ratio, 4)
                detail["viral_component"] = round(VIRAL_WEIGHT * signal, 4) if signal is not None else None
                detail["score"] = score
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
    applied = sum(1 for detail in signal_details.values() if detail["signal"] is not None)
    if not measured:
        viral_status = VIRAL_STATUS_NO_HITS
    elif not category_ready:
        viral_status = VIRAL_STATUS_INSUFFICIENT
    elif applied:
        viral_status = VIRAL_STATUS_APPLIED
    else:
        viral_status = VIRAL_STATUS_NO_HITS
    return {
        "keywords": len(collected_rows),
        "zero_coverage": len(candidates),
        "feedback_candidates": candidates,
        "viral": {
            "status": viral_status,
            "measured_samples": len(measured),
            "required": viral.MIN_CALIBRATION_SAMPLE,
            "weight": VIRAL_WEIGHT if applied else 0.0,
            "calibration": calibration_state,
            "min_keyword_hits": MIN_KEYWORD_MEASURED_HITS,
            "keywords_with_signal": applied,
            "keywords_without_signal": len(signal_details) - applied,
            # 口径说明(必须随结果一起输出):score 是综合得分,signal 只是其中权重 0.3 的加数,
            # 两者不是同一个指标,不能互相替换,也不能拿一个时间点的 score 去比另一个时间点的 signal。
            "formula": (f"score = {1.0 - VIRAL_WEIGHT:.1f} * base + {VIRAL_WEIGHT:.1f} * signal;"
                        " base = 0.6 * collected_norm + 0.4 * quality_ratio;"
                        " collected_norm = collected / max_collected;"
                        " quality_ratio = quality / collected(正文 >= 120 字);"
                        " signal = 命中文章里 tier=measured 样本的标签均值"
                        "(验证爆款 1.0 / 潜力 0.6 / 普通 0.2 / 低质 0.0);无 signal 时 score = base"),
            "score_and_signal_are_different_metrics": True,
            "details": signal_details,
        },
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
