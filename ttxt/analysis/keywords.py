"""关键词:种子维护、效果评分、反哺候选(无采集覆盖的关键词)。"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path

from ttxt import config as cfg
from ttxt.analysis import viral
from ttxt.db import connect

DEMO_SEEDS = ["人工智能", "开源", "效率工具", "数据"]

# R57(2026-11-20):别名归一表(版本化、可回滚,随类目库存 ``keyword_aliases.json``)。
# 判定口径:先归一再统计标题命中——纯字面匹配下"人工智能"标题命中 4/105(3.8%),
# 归一 AI/大模型/智能体/算力/机器人 等写法后 167/458(36.5%):只用字面算标题率
# 会把类目核心词误判成噪音词(用户 R57 裁决;逐词数字见 08_wiki/reports/r57-keyword-discrimination.md)。
# 规则 64:新增关键词必须同时给出别名,不能只填正名;改表前先另存 keyword_aliases_v<N>_<date>.json。
ALIASES_FILENAME = "keyword_aliases.json"


def _load_keyword_aliases(category_dir: Path) -> dict[str, list[str]]:
    """读类目别名归一表;不存在/读不动返回空表(按正名字面匹配,不猜别名)。"""
    path = Path(category_dir) / ALIASES_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw = data.get("aliases") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for keyword, items in raw.items():
        if not keyword or not isinstance(items, list):
            continue
        terms = [str(term) for term in items if isinstance(term, str) and term and term != keyword]
        if terms:
            out[str(keyword)] = terms
    return out

# P28(2026-11-16):真实爆款表现要喂回关键词评分。实测缺口:``score_keywords()`` 只看
# "覆盖了几篇文章 + 正文质量",完全不读 ``viral_labels`` ⇒ 同一个词采到 4 篇验证爆款
# 和采到 4 篇低质文章,评分一模一样(红证据 07_tmp/p28_viral_signal/red_evidence.txt)。
# 口径(与 viral.py 同一套词表,不另建一套):
#   1) 只认 ``confidence_tier IN ('measured','measured_reads')`` 且 ``score`` 非空 的真实测得样本;
#      measured_reads 是 T-25 的独立口径(无曝光量、按阅读量计分,阈值另标定),
#      这里只复用它的**标签词表**,不把两池的分值混算或合池重标定;
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
# T-08(2026-11-18):固定 24h 窗口挡不住"永远采不动"的 gap 词 —— technology 的 ``效率工具``
# 在 4 次反哺(job 74/76/78/149/182,共入库 48 条)后 collected 仍是 0,因为入库条目根本没有
# 这个词。冷却必须随**真实入队次数**指数退避,否则每 24h 重烧一次限速预算。
MAX_FEEDBACK_COOLDOWN_HOURS = 168.0


def _effective_cooldown_hours(base_hours: float, enqueued: int) -> float:
    """反哺冷却按真实入队次数指数退避:24h → 48h → 96h → … 封顶 7 天。

    ``base_hours <= 0`` 是运维逃生阀,保持"每轮都入队";``enqueued`` 只数**真正入队**的次数
    (冷却中跳过的不算),否则调度器每 4h 一跑会把窗口瞬间顶到上限。
    """
    if base_hours <= 0:
        return 0.0
    exponent = max(0, min(int(enqueued), 8))
    return min(base_hours * (2 ** exponent), MAX_FEEDBACK_COOLDOWN_HOURS)


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
    """本类目库里**真实测得**的爆款标签,article_id -> label。

    包含两个**互不混池**的口径:
    - ``measured``:有曝光量,按曝光率计分;
    - ``measured_reads``(T-25):无曝光量但有阅读量,按阅读量计分,阈值单独标定。

    这里只是把两者的标签词表拿来当信号(verified 1.0 / potential 0.6 / normal 0.2 / low 0.0),
    不合并分值、不共用阈值。其余一律不进样本:
    unscored / missing_data / incomplete_data / no_impressions 没有真实表现数据,
    把它们当 0 分等于凭空给关键词扣钱(规则 6:缺失 ≠ 0)。
    """
    rows = con.execute(
        "SELECT article_id, label FROM viral_labels WHERE confidence_tier IN (?, ?) AND score IS NOT NULL",
        (viral.TIER_MEASURED, viral.TIER_MEASURED_READS),
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
        # R57:判定前先按类目别名表归一(文件不存在 ⇒ 纯字面匹配,行为与旧版一致)。
        aliases = _load_keyword_aliases(target.parent)
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
                hits: list[int] = []
                title_hits: list[int] = []
            else:
                terms = [keyword, *aliases.get(keyword, [])]
                hits = [index for index in range(len(article_ids))
                        if any(term in bodies[index] or term in titles[index] for term in terms)]
                title_hits = [index for index in range(len(article_ids))
                              if any(term in titles[index] for term in terms)]
            # R57(2026-11-20)计分口径:只有**标题命中**计贡献——collected/quality/avg_len/signal
            # 只用标题命中(归一后)的文章;仅正文命中仍计入覆盖 ``hits``(判定闸照旧放行),
            # 但单列为 weak、权重为零,不再混进评分(词表准入看区分度,不看频次)。
            weak_only = [index for index in hits if index not in set(title_hits)]
            collected = len(title_hits)
            quality = sum(1 for index in title_hits if lengths[index] >= 120)
            average_length = (sum(lengths[index] for index in title_hits) / collected) if collected else 0.0
            signal, measured_hits, reason = _keyword_signal([article_ids[index] for index in title_hits], measured)
            if signal is not None and not category_ready:
                signal = None
                reason = (f"类目真实测得样本 {len(measured)} 条 < {viral.MIN_CALIBRATION_SAMPLE} 条,"
                          "阈值仍是设计假定,不宣称已标定,爆款项不参与评分")
            signal_details[keyword] = {
                "signal": signal,
                "measured_hits": measured_hits,
                "matched_articles": collected,       # 标题命中篇数(计贡献口径,别名归一后)
                "coverage_articles": len(hits),      # 标题∪正文命中(判定闸/准入判定用这个口径)
                "weak_content_hits": len(weak_only),  # 仅正文命中:单列 weak,权重零
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
                        " collected = 标题命中(别名归一后)文章数;仅正文命中记 weak、不计贡献(R57 口径);"
                        " collected_norm = collected / max_collected;"
                        " quality_ratio = quality / collected(正文 >= 120 字);"
                        " signal = 标题命中文章里 tier∈{measured, measured_reads} 样本的标签均值"
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
    cooldown_effective: float | None = None
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
                enqueued = int(entry.get("enqueued") or 0)
                effective_cooldown = _effective_cooldown_hours(cooldown_hours, enqueued)
                cooldown_effective = effective_cooldown
                cooldown_remaining = _cooldown_remaining_hours(last_enqueued_at, effective_cooldown)
                if cooldown_remaining is not None:
                    # 不入队也要计数:尝试次数是"这个 gap 词采不动"的证据,不是入队的副产品。
                    entry = {**entry, "attempts": attempts}
                    job_note = (
                        f"冷却中:距上次反哺入队还需 {cooldown_remaining:g} 小时"
                        f"(窗口 {effective_cooldown:g} 小时,已真实入队 {enqueued} 次),本次不入队;"
                        f"该来源累计第 {attempts} 次反哺尝试"
                    )
                else:
                    # T-08:反哺必须带 ``keyword_filter``,否则采回来的整源条目与 gap 词无关,
                    # ``keyword_performance.collected`` 永远是 0(实测 48 条入库后仍是 0)。
                    job_id = store.create_crawl_job(
                        target_source.id,
                        category,
                        db_path=system_db,
                        meta={"reason": "keyword_gap_feedback", "keywords": gaps,
                              "keyword_filter": gaps,
                              "dispatched_via": "keyword_feedback", "attempt": attempts},
                    )
                    job_note = "已入队补充采集任务(不自动执行;入库侧按 gap 词过滤)"
                    last_enqueued_at = cfg.now_iso()
                    entry = {"last_enqueued_at": last_enqueued_at, "attempts": attempts,
                             "enqueued": enqueued + 1}
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
        "cooldown_hours_effective": cooldown_effective,
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


# ---------------------------------------------------------------------------
# R28:关键词发现(闭环缺的另一半)
#
# 实测缺口(2026-11-18):``keywords`` 表的来源只有 ``seed_keywords``(origin='seed'),
# 全项目没有任何"从语料里长出新词"的代码 ⇒ ``auto_feedback`` 只能反复为**同样那几个种子词**
# 入队采集。目标②要的是"模型知道什么是爆款 ⇒ 把学到的话题反馈给采集",缺了这一步,
# 系统永远只会在三个种子词里打转,不可能越来越聪明。
#
# 口径(全部可复算,不引入模型调用、不装分词词典——要挖的正是词典里没有的新词):
#   - 只从**标题**取词:中文连续片段切 2-4 字 n-gram(跨标点的拼接一律不取);
#   - 爆款样本 = ``viral_labels`` 里 label 为 verified_viral / potential 的文章;
#   - 打分 = lift × sqrt(爆款命中篇数),lift = 爆款中出现率 / 全库出现率;
#   - 只收在 ≥ ``min_docs`` 篇**不同**爆款文章里出现、且 lift ≥ ``min_lift`` 的词;
#   - 五道"碎片/通用词闸门"(R28 实测:纯 n-gram 首轮 10 个词里 6 个是跨词边界的碎片):
#       ① 首尾虚词拒收;② **出现次数**级别的延长判定:若某候选的全部出现都被同一邻字
#          延长成更长的串(occ 完全相等),它就不是词;③ 左右邻字熵 ≥ ``_ENTROPY_MIN``:
#          真词能跟不同字搭配,碎片只能跟固定那几个字搭配;④ 类目特异性 ≥
#          ``MINE_MIN_SPECIFICITY``:每个类目都一样常见的词是通用词;⑤ 词性:纯副词/
#          时间/数词/代词/动词不是话题,首尾切出单字说明跨了词边界;
#   - 停用词、纯数字/字母、与现有词互为子串的候选一律丢弃(不制造近重复关键词);
#   - 旧挖词退场(top_n 之外的 origin='mined' 连带评分行一起删);语料太少不出手;
#   - **类目没标定就不挖**:阈值还是设计假定,据此挖词等于把假说当事实(与 score_keywords 同口径)。
MINE_ORIGIN = "mined"
MINE_MIN_DOCS = 3
MINE_MIN_LIFT = 2.0
MINE_TOP_N = 10
# 挖词是"当前在火什么",不是全库考古:只挖最近 N 篇,顺便把复杂度钉在可控范围。
MINE_MAX_TITLES = 20000
# 语料少于这个数不做"旧挖词退场":一轮语料不够就把词表清空是自伤。
MINE_PRUNE_MIN_TITLES = 20
_NGRAM_MIN = 2
_NGRAM_MAX = 4       # 候选词长度上限
_COUNT_MAX = 5       # 统计到 5 字:4 字候选要靠"能不能再延长一个字"判碎片
_ENTROPY_MIN = 0.5   # 左右邻字熵下限(自然对数):0.5 ≈ 至少两种邻字且不极端偏斜
# 第四道闸门「类目特异性」:同一个词在本类目的出现率 / 其他类目出现率的中位数。
# 实测(2026-11-18 technology):「最后 / 已有 / 年后 / 安全 / 亿美元」全都能过前三道闸门,
# 但它们在每个类目的标题里都一样常见 —— 那是通用词,不是话题。真话题(航天/神舟二十)
# 只在某一个类目里密集出现,比值差一个数量级以上。
MINE_MIN_SPECIFICITY = 2.0
MINE_BACKGROUND_TITLES = 5000   # 每个其他类目最多取多少条标题当背景(只读标题,不读标签)
# R28 实测(2026-11-18 social):类目特异性挡不住「落地/企业」这类**全站通用**词——它们在
# 其他类目也不常见(其他类目是科技/财经/历史),但在本类目里到处都是,做采集词只会拉回噪音。
# 判据用"出现率"而不是绝对篇数:18000 个标题里 3% 以上的词是通用词,不是话题。
# 小语料(< 2000 个标题)不启用:197 篇历史语料里「毛主席」占 7.6%,但它确实是话题。
MINE_MAX_PREVALENCE = 0.02
MINE_MAX_PREVALENCE_MIN_TITLES = 2000
# 第五道闸门「词性」:纯副词/时间词/数词/量词/代词/动词的 n-gram 不是话题。
# 实测(2026-11-18 technology):「最后 / 已有 / 年后 / 亿美元」都能过前四道闸门(类目特异性
# 也有 2.0~4.0),但词性分别是 f / v / m+f / m —— 它们不是话题,是句子成分。
# 另:首/尾切出单字词(且整体 ≥3 字)说明 n-gram 跨过了词边界(中国科 → 中国|科)。
_POS_REJECT = frozenset({
    "f", "t", "m", "q", "d", "r", "p", "c", "u", "y", "e", "o",
    "v", "vd", "vn", "a", "ad", "an",
})
_POS_READY = False
_CJK_RUN = re.compile(r"[\u4e00-\u9fa5]+")
_VIRAL_LABELS = (viral.LABEL_VERIFIED, viral.LABEL_POTENTIAL)
# 标题里的高频虚词/套话:进了关键词只会污染覆盖统计与采集过滤,不会带来新话题
STOPWORDS = frozenset({
    "我们", "他们", "她们", "你们", "自己", "大家", "有人", "别人",
    "这个", "那个", "这些", "那些", "这样", "那样", "这种", "那种",
    "一个", "一种", "一些", "什么", "怎么", "怎样", "如何", "为何", "为什么",
    "可以", "没有", "不是", "就是", "已经", "还是", "但是", "因为", "所以",
    "如果", "不过", "而且", "并且", "然后", "现在", "时候", "今天", "昨天", "明天",
    "记者", "报道", "视频", "网友", "评论", "曝光", "揭秘", "真相", "震惊",
    "注意", "提醒", "建议", "一定", "千万", "必须", "不要", "需要",
    "表示", "认为", "发现", "最新", "重要", "到底", "居然", "原来",
    "事情", "问题", "方法", "情况", "方面", "时间", "地方", "东西", "真的", "其实",
    "专家", "数据显示", "值得注意",
    # R28 实测第二轮(369 篇 technology 标题):留下的 11 个候选里还有 6 个不是话题的词。
    # 「二十」这类纯数词另用 _NUMERAL_CHARS 判,其余在这里补。
    "突然", "完成", "没人", "世界", "国人",
})
# R28 实测:纯数词串(二十 / 三百)没有任何话题含义,不该进关键词表。
_NUMERAL_CHARS = frozenset("〇零一二三四五六七八九十百千万亿两")
# R28 实测(technology 首次跑出 10 个词里有 6 个是碎片:的地/国这/何中/为何中…):
# 以这些虚词开头或结尾的 n-gram 一定是跨词边界切出来的碎片,直接拒。
_PARTICLES = frozenset("的了是在和与及也都就被把让对从到为这那之其而且个们着过吧呢吗啊")


def _cjk_runs(text: str) -> list[str]:
    return _CJK_RUN.findall(text or "")


def _fragment_grams(occurrences: dict[str, int]) -> set[str]:
    """找出"全部出现都被同一邻字延长"的碎片(R28 第二道闸门)。

    判据用**出现次数**而不是文档数:``为何中`` 与 ``为何中国`` 的出现次数相等 ⇒ 每一次
    ``为何中`` 后面都跟着 ``国``,它只是 ``为何中国`` 的前缀,不单独成词。
    文档数口径做不到这件事(同一篇里既有 ``中国科学`` 又有 ``中国科技`` 时,``中国科``
    的文档数会大于任何一个更长的串,于是永远漏掉)。
    """
    fragments: set[str] = set()
    for gram, count in occurrences.items():
        if len(gram) < _NGRAM_MIN + 1:
            continue
        for part in (gram[:-1], gram[1:]):
            if len(part) >= _NGRAM_MIN and occurrences.get(part, 0) == count:
                fragments.add(part)
    return fragments


def _neighbor_entropy(
    candidates: set[str], titles: list[str]
) -> dict[str, tuple[float, float]]:
    """候选词的左右邻字熵(第三道闸门):真词能跟不同字搭配,碎片只能跟固定字搭配。

    标题首尾用 ``^`` / ``$`` 当成一种邻字参与统计(总在句首出现的词也会被如实记为低熵)。
    """
    left: dict[str, dict[str, int]] = {}
    right: dict[str, dict[str, int]] = {}
    for title in titles:
        for run in _cjk_runs(title):
            length = len(run)
            for size in range(_NGRAM_MIN, _NGRAM_MAX + 1):
                for start in range(length - size + 1):
                    gram = run[start:start + size]
                    if gram not in candidates:
                        continue
                    left_char = run[start - 1] if start else "^"
                    right_char = run[start + size] if start + size < length else "$"
                    left.setdefault(gram, {})
                    left[gram][left_char] = left[gram].get(left_char, 0) + 1
                    right.setdefault(gram, {})
                    right[gram][right_char] = right[gram].get(right_char, 0) + 1

    def _entropy(counter: dict[str, int] | None) -> float:
        if not counter:
            return 0.0
        total = sum(counter.values())
        return -sum((count / total) * math.log(count / total) for count in counter.values())

    return {gram: (_entropy(left.get(gram)), _entropy(right.get(gram))) for gram in candidates}


def _pos_rejected(gram: str) -> tuple[bool, str]:
    """词性闸门:返回 ``(是否拒绝, 词性串)``。

    没有分词器时**不启用**这道闸门(返回 ``(False, "")``):装不上依赖不能把挖词停掉,
    但也不能退化成"全过"之外的第二种瞎猜方式。
    """
    global _POS_READY
    try:
        import jieba
        import jieba.posseg as pseg
    except Exception:  # noqa: BLE001 - 缺依赖时跳过闸门
        return False, ""
    if not _POS_READY:
        # jieba 会在当前目录写 jieba.cache:钉到 07_tmp,不给仓库根目录留垃圾
        jieba.dt.tmp_dir = str(cfg.SCRATCH_DIR)
        _POS_READY = True
    tokens = [(word, flag) for word, flag in pseg.cut(gram) if word.strip()]
    if not tokens:
        return True, ""
    flags = "/".join(flag for _, flag in tokens)
    if all(flag in _POS_REJECT for _, flag in tokens):
        return True, flags
    if len(gram) >= 3 and (len(tokens[0][0]) == 1 or len(tokens[-1][0]) == 1):
        return True, flags
    return False, flags


def _background_corpora(category: str, own_db: Path) -> list[tuple[str, list[str]]]:
    """其他类目的**标题**(只读标题,不读标签:类目隔离不能破)。

    用途只有一个:判断一个词是不是"通用词"。取不到(其他类目还没建库/读失败)就返回空,
    调用方据此跳过类目特异性闸门,而不是把词全判成通用词。
    """
    try:
        slugs = [
            str(item.get("slug"))
            for item in cfg.load_registry().get("categories", [])
            if item.get("slug")
        ]
    except Exception:  # noqa: BLE001 - 注册表读不出来就当没有背景语料
        return []
    own = Path(own_db).resolve()
    corpora: list[tuple[str, list[str]]] = []
    for slug in slugs:
        if slug == category:
            continue
        path = cfg.CATEGORIES_DIR / slug / "articles.db"
        if not path.exists() or path.resolve() == own:
            continue
        try:
            con = connect(path)
        except Exception:  # noqa: BLE001 - 单个类目库坏了不能拖累挖词
            continue
        try:
            titles = [
                str(row["title"])
                for row in con.execute(
                    "SELECT title FROM articles ORDER BY id DESC LIMIT ?", (MINE_BACKGROUND_TITLES,)
                )
                if str(row["title"] or "").strip()
            ]
        except Exception:  # noqa: BLE001
            titles = []
        finally:
            con.close()
        if titles:
            corpora.append((slug, titles))
    return corpora


def _drop_generic_candidates(
    candidates: dict[str, dict],
    doc_freq: dict[str, int],
    total_docs: int,
    background: list[tuple[str, list[str]]],
    min_specificity: float,
) -> dict[str, dict]:
    """类目特异性闸门:只在本类目密集出现的词才算话题候选。

    ``specificity = 本类目出现率 / 其他类目出现率的中位数``。其他类目都几乎不出现 ⇒ 比值大;
    每个类目标题里都一样的词(最后/已有/安全…)⇒ 比值接近 1,不是话题。
    没有背景语料时不启用这道闸门(不凭空判通用词)。
    """
    if not background:
        return candidates
    floor = 1.0 / max(1, total_docs)   # 其他类目 0 次时给个下限,避免除零把比值吹成无穷
    kept: dict[str, dict] = {}
    for gram, item in candidates.items():
        own_prev = doc_freq[gram] / total_docs
        prevalences = sorted(
            sum(1 for title in titles if gram in title) / len(titles) for _, titles in background
        )
        base = prevalences[len(prevalences) // 2]
        specificity = own_prev / max(base, floor)
        if specificity < min_specificity:
            continue
        item["specificity"] = round(specificity, 3)
        kept[gram] = item
    return kept


def mine_keywords(
    category: str,
    db_path: Path | None = None,
    min_docs: int = MINE_MIN_DOCS,
    min_lift: float = MINE_MIN_LIFT,
    top_n: int = MINE_TOP_N,
    specificity_min: float = MINE_MIN_SPECIFICITY,
    background: list[tuple[str, list[str]]] | None = None,
) -> dict:
    """从**已标定的**爆款标题里挖新关键词,写入 ``keywords``(origin='mined')。

    只读本类目,不改已有词、不入队(入队仍由 ``auto_feedback`` 在下一轮按 gap 决定);
    返回的 ``top`` 是完整打分明细,便于任何一次引用都能复算。
    ``background`` 传 ``None`` ⇒ 自动读其他类目标题当通用词背景(类目隔离:只读标题);
    传 ``[]`` ⇒ 关闭类目特异性闸门(测试用,保证结果只取决于本库)。
    """
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    con = connect(target)
    try:
        calibration = viral.load_calibration(con)
        if not calibration:
            viral_docs = con.execute(
                "SELECT COUNT(DISTINCT article_id) FROM viral_labels WHERE label IN (%s)"
                % ",".join("?" * len(_VIRAL_LABELS)),
                _VIRAL_LABELS,
            ).fetchone()[0]
            return {"status": "skipped_uncalibrated", "viral_docs": int(viral_docs),
                    "candidates": 0, "inserted": 0, "note": "类目未标定,不据设计假定阈值挖词"}
        viral_ids = {
            int(row["article_id"])
            for row in con.execute(
                "SELECT DISTINCT article_id FROM viral_labels WHERE label IN (%s)"
                % ",".join("?" * len(_VIRAL_LABELS)),
                _VIRAL_LABELS,
            )
        }
        # R28 实测(2026-11-18 social):同一标题被上百篇文章重复搬用,连爆款标签也一起复制
        # (「物理AI进入规模化验证期…」4 篇),于是「入规/成资」这种跨词边界碎片被当成
        # "4 篇不同爆款都在用"的话题。**同一个标题是同一次编辑选择,不是多次证据** ⇒
        # 按标题去重,只要有一篇是同标题爆款,这个标题就算爆款标题。
        unique_titles: dict[str, list] = {}
        raw_rows = 0
        for row in con.execute(
            "SELECT id, title FROM articles ORDER BY id DESC LIMIT ?", (MINE_MAX_TITLES,)
        ):
            text = str(row["title"] or "").strip()
            if not text:
                continue
            raw_rows += 1
            article_id = int(row["id"])
            entry = unique_titles.get(text)
            if entry is None:
                unique_titles[text] = [article_id, article_id in viral_ids]
            elif article_id in viral_ids and not entry[1]:
                entry[1] = True
        titles = [(article_id, text, is_viral)
                  for text, (article_id, is_viral) in unique_titles.items()]
        duplicate_titles = raw_rows - len(titles)
        # 近重复判定只看**非挖词**来源:挖出来的词每轮重算,让上一轮的挖词挡住本轮候选
        # 会让词表卡在旧说法上(实测:上一版算法留下的「中国科」会挡掉更准的「中国科学」)。
        existing = [
            str(row["keyword"])
            for row in con.execute("SELECT keyword FROM keywords WHERE origin<>?", (MINE_ORIGIN,))
        ]
        viral_in_corpus = {article_id for article_id, _, is_viral in titles if is_viral}
        if len(viral_in_corpus) < min_docs or not titles:
            return {"status": "no_data", "viral_docs": len(viral_in_corpus),
                    "candidates": 0, "inserted": 0}

        total_docs = len(titles)
        doc_freq: dict[str, int] = {}
        occurrences: dict[str, int] = {}
        viral_doc_freq: dict[str, int] = {}
        for article_id, title, is_viral in titles:
            for run in _cjk_runs(title):
                length = len(run)
                seen: set[str] = set()
                for size in range(_NGRAM_MIN, _COUNT_MAX + 1):
                    for start in range(length - size + 1):
                        gram = run[start:start + size]
                        occurrences[gram] = occurrences.get(gram, 0) + 1
                        if gram in seen:
                            continue
                        seen.add(gram)
                        doc_freq[gram] = doc_freq.get(gram, 0) + 1
                        if is_viral:
                            viral_doc_freq[gram] = viral_doc_freq.get(gram, 0) + 1
        fragments = _fragment_grams(occurrences)
        candidates: dict[str, dict] = {}
        for gram, viral_docs in viral_doc_freq.items():
            if len(gram) > _NGRAM_MAX or viral_docs < min_docs or gram in STOPWORDS:
                continue
            if gram in fragments or gram[0] in _PARTICLES or gram[-1] in _PARTICLES:
                continue
            if all(char in _NUMERAL_CHARS for char in gram):
                continue
            pos_rejected, pos = _pos_rejected(gram)
            if pos_rejected:
                continue
            # 通用词闸门:大语料里到处都有的词做采集词只会拉回噪音(小语料不启用,见常量注释)
            if (total_docs >= MINE_MAX_PREVALENCE_MIN_TITLES
                    and doc_freq[gram] / total_docs >= MINE_MAX_PREVALENCE):
                continue
            if any(gram in word or word in gram for word in existing):
                continue
            lift = (viral_docs / len(viral_in_corpus)) / (doc_freq[gram] / total_docs)
            if lift < min_lift:
                continue
            candidates[gram] = {
                "keyword": gram,
                "viral_docs": viral_docs,
                "lift": round(lift, 3),
                "score": round(lift * math.sqrt(viral_docs), 3),
                "pos": pos,
            }
        # 类目特异性只对候选集统计:每个候选 × 每个背景类目做一次子串扫描,与库大小无关。
        if background is None:
            background = _background_corpora(category, target)
        candidates = _drop_generic_candidates(candidates, doc_freq, total_docs, background, specificity_min)
        # 熵只对候选集算一遍:每个位置最多 3 次哈希查找,与库大小线性相关。
        entropy = _neighbor_entropy(set(candidates), [title for _, title, _ in titles])
        scored: list[dict] = []
        for gram, item in candidates.items():
            left_entropy, right_entropy = entropy.get(gram, (0.0, 0.0))
            if min(left_entropy, right_entropy) < _ENTROPY_MIN:
                continue
            item["left_entropy"] = round(left_entropy, 3)
            item["right_entropy"] = round(right_entropy, 3)
            scored.append(item)
        # 最长优先去碎片:同一批文章里既切出「为何中」又切出「为何中国」时,只保留长的那个。
        # 判据是**爆款命中篇数完全相等**(说明短词在这些文章里没有任何额外覆盖),不是凭长度拍脑袋。
        by_docs: dict[int, list[str]] = {}
        for item in scored:
            by_docs.setdefault(item["viral_docs"], []).append(item["keyword"])
        kept: list[dict] = []
        for item in sorted(scored, key=lambda entry: (-len(entry["keyword"]), entry["keyword"])):
            word = item["keyword"]
            if any(word != other and word in other for other in by_docs[item["viral_docs"]]):
                continue
            kept.append(item)
        scored = kept
        scored.sort(key=lambda item: (-item["score"], -item["viral_docs"], item["keyword"]))
        picked = scored[: max(0, int(top_n))]
        inserted = 0
        stamp = cfg.now_iso()
        for item in picked:
            cur = con.execute(
                "INSERT OR IGNORE INTO keywords(keyword, category, origin, status, created_at)"
                " VALUES (?, ?, ?, 'active', ?)",
                (item["keyword"], category, MINE_ORIGIN, stamp),
            )
            inserted += int(cur.rowcount or 0)
        # 挖词是滚动话题:这一轮不再产出的旧挖词必须退场,否则词表只增不减、gap 反哺永远
        # 围着早就过时的词打转(实测:上一版算法留下的 10 个碎片词一直留在 technology 里)。
        # 语料太少时不动(不能因为一轮语料不够就把词表清空)。
        pruned = 0
        if total_docs >= MINE_PRUNE_MIN_TITLES:
            keep = [item["keyword"] for item in picked]
            target_ids = [
                int(row["id"])
                for row in con.execute(
                    "SELECT id, keyword FROM keywords WHERE origin=? AND category=?", (MINE_ORIGIN, category)
                )
                if str(row["keyword"]) not in keep
            ]
            for keyword_id in target_ids:
                con.execute("DELETE FROM keyword_performance WHERE keyword_id=?", (keyword_id,))
                con.execute("DELETE FROM keywords WHERE id=?", (keyword_id,))
                pruned += 1
        # 自愈:历史清理(手动删词、旧版本没有配套删 performance)会留下指向已不存在的
        # keyword_id 的评分行。它们不参与任何 join,但会让"词表到底有多少词"的核对永远对不上。
        con.execute(
            "DELETE FROM keyword_performance WHERE keyword_id NOT IN (SELECT id FROM keywords)"
        )
        con.commit()
        return {
            "status": "ok",
            "viral_docs": len(viral_in_corpus),
            "corpus_titles": total_docs,
            "duplicate_titles_dropped": duplicate_titles,
            "candidates": len(scored),
            "inserted": inserted,
            "pruned": pruned,
            "min_docs": min_docs,
            "min_lift": min_lift,
            "specificity_min": specificity_min,
            "max_prevalence": MINE_MAX_PREVALENCE,
            "background_categories": len(background),
            "entropy_min": _ENTROPY_MIN,
            "top": picked,
        }
    finally:
        con.close()
