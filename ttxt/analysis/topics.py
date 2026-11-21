"""R64:数据驱动选题 —— 由系统自己提出「语料支撑得起」的主题,而不是靠人拍脑袋。

R64-1 之后,离题素材不再占素材槽:取材门禁会把「语料里根本没有该主题」的选题在
调用模型**之前**挡下(``status="insufficient_materials"``,零模型调用)。R50/R63 的
实测现场也一样:10 个候选主题里只有 0 个写得出来,原因是语料缺口,不是模型能力。
所以选题这一刀必须从「人工出题」升级为「按库里数据出题 + 预先自评支持度」。

候选主题来源(全部来自库里真实数据,不编主题):
1. ``keyword_performance`` 的类目关键词战绩(``ttxt analyze`` 落库的真实统计,score 降序,
   带 ``origin`` 溯源:``seed`` 是人给的、``mined`` 是系统从**已标定爆款标题**里挖出来的
   (见 ``analysis.keywords.mine_keywords``)⇒「爆款 ⇒ 关键词 ⇒ 主题」这条链项目里已有
   实现并有五道碎片闸门,本模块只消费它的产物,不自造第二套挖词;缺表/缺行如实报空,
   不虚构加分 —— 规则 6);
2. ``--topic`` 显式补题(人工/审计用:某题到底有没有素材,当场给数)。

爆款标题**不进候选池**,只作参考线索(``viral_reference_titles``)。理由是社会层实测
(2026-11-21,social 类目 28 个候选):整条标题当主题会两头失真 ——
被取材门禁拦下的 11 个候选里 10 个是整条爆款标题(FTS 按标题全部 token 做 AND,
只召回它自己:相关全文 1、独立来源 1);却另有 2 条整标题靠标题里的常见二字组
**蒙过**门禁(相关全文 15/15、离题剔除 0 —— 那是检索假象,不是语料真撑得起)。
同一把尺子对整标题既误杀又误放 ⇒ 支持度只用在「词/短语」级主题上。

支持度口径(先声明后取数,防挑数):对每个候选主题跑**与 write_article 完全同一条
取材链** —— ``rag.search(limit=MATERIAL_POOL_LIMIT)`` + ``workflow._rank_materials(topic=…)``
(R64-1 的相关度过滤自动生效),再用 ``workflow._material_gate`` 预判取材门禁。
判定与阈值全部复用既有实现:``material_min_full=6``、``material_min_sources=3``,
不新增阈值、不放宽任何门禁。

排序规则(声明先于结果):放行优先 ⇒ 再比 ``kept_full``(相对 6 全文门槛的余量)
⇒ ``distinct_sources``(独立来源余量)⇒ ``kept``(相关稿数)⇒ 来源战绩分。
已知上限(实测):``kept_full`` 受检索窗口截断(窄主题 15 封顶),余量只用来**排序**、
不用来比「谁更值得写」;能不能写由门禁决定,不是名次。

被门禁拦下的候选:输出**采集缺口**与可直接执行的 ``crawl-plan`` 建议命令
(默认干跑,绝不入队、绝不动生产、绝不触发调度器)。

红线:本模块只读类目库(不写任何业务表),不调模型,不改任何既有判定与阈值。
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from ttxt import config as cfg
from ttxt.analysis import rag, viral
from ttxt.crawler import cleaner, store
from ttxt.db import connect

KEYWORD_SOURCE = "keyword_performance"
CLI_SOURCE = "cli"
# 进候选池的来源只有这两个;爆款标题走 REF_SOURCE(参考线索,不参与支持度排序)。
CANDIDATE_SOURCES = (KEYWORD_SOURCE,)
REF_SOURCE = "viral_reference_titles"
VIRAL_TIERS = (viral.TIER_MEASURED, viral.TIER_MEASURED_READS)
# 参考线索只认「真实数据测得的爆款/潜质爆款」:normal 是中性样本(填榜噪声),
# low_quality 是用户明确要排除的垃圾(目标 2「哪些文章是垃圾」)。
VIRAL_REF_LABELS = (viral.LABEL_VERIFIED, viral.LABEL_POTENTIAL)
VIRAL_SCAN_MULTIPLIER = 4  # 先扫这一窗口的真实标签,再按标签过滤并计数,不做静默丢弃

# 参考标题的形状约束:太短(如「成本」)不成线索,太长读不动。
# 跳过数如实写进证据文件,不静默丢弃(与候选池「跳了几条、为什么」同一纪律)。
REF_TITLE_MIN_CHARS = 6
REF_TITLE_MAX_CHARS = 60

# 爆款 → 主题的既有通道:挖词落 keywords(origin='mined'),采集由 auto_feedback 决定。
# 这里只给出路,不自己实现,也不替系统入队(红线:调度器绝不触发写作/发布)。
VIRAL_TO_KEYWORD_COMMAND = "ttxt analyze --category {category}(内部跑 mine_keywords,只写 keywords)"

EVIDENCE_DIRNAME = "topic_support"
_WS = re.compile(r"\s+")
_TITLE_NOTE = (
    "整条爆款标题不是主题:FTS 按标题 token 做 AND ⇒ 通常只召回它自己(误杀),"
    "长标题里的常见二字组又能把它蒙过门禁(误放)⇒ 不参与支持度排序。"
    "要把爆款变成主题,走既有挖词通道(analyze → mine_keywords ⇒ keywords.origin='mined'),"
    "由 auto_feedback 决定要不要补采集。"
)


def normalize_topic(raw: str) -> str:
    """主题串归一:压空白、去首尾;空串返回空(调用方据此跳过,不猜主题)。"""
    return _WS.sub(" ", str(raw or "")).strip()


def resolve_db_path(category: str, db_path: Path | str | None = None) -> Path:
    """缺省取类目正式库;测试/巡检必须显式传快照库(config.PROJECT_ROOT 硬编码 D:/ttxt)。"""
    return store.articles_db_path(category) if db_path is None else Path(db_path)


def writing_thresholds() -> dict:
    """门禁阈值只从配置的 writing 段读(与 write_article 同一来源,不硬编码)。"""
    writing = cfg.load_config().get("writing", {})
    return {
        "min_full": int(writing.get("material_min_full", 6)),
        "min_sources": int(writing.get("material_min_sources", 3)),
    }


def corpus_support(category: str, topic: str, db_path: Path | str | None = None) -> dict:
    """一个主题在既有语料里的可写支持度 —— 与写作同一条取材链、同一套门禁判定。

    惰性导入 ``workflow``:analysis 层不得在模块级依赖 writing 层(会成环),
    但取材/门禁实现必须复用同一份,不能在这里重写一遍判定。
    """
    from ttxt.writing import workflow  # 惰性导入:避免 analysis ←→ writing 循环

    target = resolve_db_path(category, db_path)
    topic = normalize_topic(topic)
    matches = rag.search(category, topic, limit=workflow.MATERIAL_POOL_LIMIT, db_path=target)
    _materials, stats = workflow._rank_materials(matches, target, topic=topic)
    thresholds = writing_thresholds()
    critical = workflow._material_gate(stats, thresholds["min_full"], thresholds["min_sources"])
    rel = stats.get("relevance", {})
    return {
        "topic": topic,
        "searched": stats["searched_candidates"],
        "kept": stats["candidates"],
        "kept_full": stats["full_candidates"],
        "kept_short": stats["candidates"] - stats["full_candidates"],
        "dropped_off_topic": rel.get("dropped", 0),
        "dropped_off_topic_full": rel.get("dropped_full", 0),
        "distinct_sources": stats["distinct_sources"],
        "distinct_hosts": stats["distinct_hosts"],
        "independence_axis": stats.get("independence_axis", {}),
        "relevance_rule": rel.get("rule", ""),
        "topic_tokens": rel.get("tokens", 0),
        "off_topic_samples": rel.get("off_topic_samples", []),
        "pool_limit": stats["pool_limit"],
        "thresholds": thresholds,
        "gate": "pass" if not critical else "block",
        "critical": critical,
    }


def keyword_candidates(category: str, db_path: Path | str | None = None, limit: int = 12) -> dict:
    """类目关键词战绩榜(score 降序)。缺表/缺战绩 ⇒ 如实报空,不做假设。

    ``origin`` 必须带出来:``mined`` 的词本身就是上一轮「爆款标题 → 挖词」的产物,
    没有它就没法审计「这个主题是系统学的还是人塞的」。

    R65(2026-11-21):``exhausted`` 的词**仍然算主题候选**。那个状态只说明"采集侧带不回
    新文章"(别再烧采集预算),不代表语料撑不起这篇稿子 —— 实测 social 的 ``闷声发财``
    标题命中 399 篇却一篇新的都没采回,它照样是当下最能写的主题之一。被排除的是
    ``gap``(库里根本没有)和 ``weak``(仅正文命中的宽词,不是话题)。
    """
    target = resolve_db_path(category, db_path)
    con = connect(target)
    try:
        rows = con.execute(
            "SELECT k.keyword, k.origin, k.status, p.score, p.collected, p.quality_articles, p.updated_at"
            " FROM keywords k LEFT JOIN keyword_performance p ON p.keyword_id = k.id"
            " WHERE k.category = ? AND k.status IN ('active', 'exhausted')"
            " ORDER BY p.score IS NULL, p.score DESC, p.collected DESC, k.id ASC LIMIT ?",
            (category, limit),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return {"source": KEYWORD_SOURCE, "status": "table_missing", "detail": str(exc), "items": []}
    finally:
        con.close()
    items = []
    for row in rows:
        topic = normalize_topic(row["keyword"])
        if not topic:
            continue
        items.append(
            {
                "topic": topic,
                "ref": row["keyword"],
                "origin": row["origin"],
                "keyword_status": row["status"],
                "score": row["score"],
                "collected": row["collected"],
                "quality_articles": row["quality_articles"],
                "scored_at": row["updated_at"],
            }
        )
    scored = [item for item in items if item["score"] is not None]
    return {
        "source": KEYWORD_SOURCE,
        "status": "ok" if scored else "no_performance_rows",
        "origins": sorted({str(item["origin"]) for item in items}),
        "note": (
            "类目库没有关键词战绩(先跑 ttxt analyze / ttxt keywords-feedback)"
            if not scored else ""
        ),
        "items": items,
    }


def viral_reference_titles(category: str, db_path: Path | str | None = None, limit: int = 12) -> dict:
    """库里真实测得的「爆款/潜质爆款」标题:只当参考线索,不进候选池(理由见 _TITLE_NOTE)。

    口径(先声明):
    - 只认 ``confidence_tier IN ('measured','measured_reads')``:与 ``viral.calibrate``
      同一立场,没有真实测得数据的标签不算证据(规则 6,不拿缺数据当 0 分也不虚构);
    - 只认 ``label IN ('verified_viral','potential')``:``normal`` 是中性样本(填榜噪声),
      ``low_quality`` 是用户明确要排除的垃圾(目标 2),被排除的标签逐个数;
    - 只取 ``completeness='full'``:短稿(RSS 摘要)的标题连原文都撑不起;
    - 太短/太长/为空一律跳过并计数(不静默丢,证据文件里能看到跳了几条、为什么)。
    """
    target = resolve_db_path(category, db_path)
    tiers = ",".join("?" for _ in VIRAL_TIERS)
    con = connect(target)
    try:
        rows = con.execute(
            f"SELECT a.id, a.title, a.url, l.label, l.confidence_tier, l.score, l.labeled_at"
            f" FROM viral_labels l JOIN articles a ON a.id = l.article_id"
            f" WHERE l.confidence_tier IN ({tiers}) AND a.completeness = ?"
            f" ORDER BY l.score IS NULL, l.score DESC, a.id ASC LIMIT ?",
            (*VIRAL_TIERS, cleaner.COMPLETENESS_FULL, limit * VIRAL_SCAN_MULTIPLIER),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return {"source": REF_SOURCE, "status": "table_missing", "detail": str(exc), "items": [],
                "note": _TITLE_NOTE}
    finally:
        con.close()
    items: list[dict] = []
    skipped = {"too_short": 0, "too_long": 0, "empty": 0, "scanned": len(rows)}
    excluded_by_label: dict[str, int] = {}
    for row in rows:
        if len(items) >= limit:
            skipped["over_limit"] = skipped.get("over_limit", 0) + 1
            continue
        if row["label"] not in VIRAL_REF_LABELS:
            excluded_by_label[row["label"]] = excluded_by_label.get(row["label"], 0) + 1
            continue
        title = normalize_topic(row["title"])
        if not title:
            skipped["empty"] += 1
            continue
        if len(title) < REF_TITLE_MIN_CHARS:
            skipped["too_short"] += 1
            continue
        if len(title) > REF_TITLE_MAX_CHARS:
            skipped["too_long"] += 1
            continue
        items.append(
            {
                "title": title,
                "article_id": row["id"],
                "url": row["url"],
                "score": row["score"],
                "label": row["label"],
                "confidence_tier": row["confidence_tier"],
                "labeled_at": row["labeled_at"],
            }
        )
    return {
        "source": REF_SOURCE,
        "status": "ok" if items else "no_viral_reference_titles",
        "used_for": "reference_only_not_topic",
        "note": _TITLE_NOTE,
        "tiers": list(VIRAL_TIERS),
        "reference_labels": list(VIRAL_REF_LABELS),
        "title_char_range": [REF_TITLE_MIN_CHARS, REF_TITLE_MAX_CHARS],
        "scanned": len(rows),
        "excluded_by_label": excluded_by_label,
        "skipped": skipped,
        "items": items,
    }


def collect_candidates(
    category: str,
    db_path: Path | str | None = None,
    *,
    pool: int = 12,
    extra_topics: tuple[str, ...] | list[str] = (),
    sources: tuple[str, ...] = CANDIDATE_SOURCES,
) -> tuple[list[dict], dict]:
    """候选来源合流去重(同一主题只评一次,来源与战绩并列保留)。"""
    target = resolve_db_path(category, db_path)
    pools: dict[str, dict] = {}
    if KEYWORD_SOURCE in sources:
        pools[KEYWORD_SOURCE] = keyword_candidates(category, target, limit=pool)

    merged: dict[str, dict] = {}
    for source_name, pool_data in pools.items():
        for item in pool_data["items"]:
            entry = merged.setdefault(item["topic"], {"topic": item["topic"], "sources": []})
            entry["sources"].append({"kind": source_name, **item})
    for raw in extra_topics:
        topic = normalize_topic(raw)
        if not topic:
            continue
        entry = merged.setdefault(topic, {"topic": topic, "sources": []})
        if not any(src["kind"] == CLI_SOURCE for src in entry["sources"]):
            entry["sources"].append({"kind": CLI_SOURCE, "topic": topic, "ref": raw, "score": None})
    return list(merged.values()), pools


def _best_signal(entry: dict) -> float:
    scores = [src.get("score") for src in entry["sources"] if src.get("score") is not None]
    return max(scores) if scores else -1.0


def rank_topics(
    category: str,
    db_path: Path | str | None = None,
    *,
    limit: int = 8,
    pool: int = 12,
    extra_topics: tuple[str, ...] | list[str] = (),
    sources: tuple[str, ...] = CANDIDATE_SOURCES,
    references: bool = True,
) -> dict:
    """候选主题的完整评估证据包(排序规则见模块 docstring;判定复用 _material_gate)。"""
    target = resolve_db_path(category, db_path)
    # 来源名写错(或把「爆款标题」当来源)必须当场报出来:否则候选 0 条会被读成「库里没主题」。
    unknown_sources = [name for name in sources if name not in CANDIDATE_SOURCES]
    candidates, pools = collect_candidates(
        category, target, pool=pool, extra_topics=extra_topics, sources=sources
    )
    evaluated: list[dict] = []
    for entry in candidates:
        support = corpus_support(category, entry["topic"], target)
        evaluated.append(
            {
                "topic": entry["topic"],
                "sources": entry["sources"],
                "best_signal": None if _best_signal(entry) < 0 else _best_signal(entry),
                "support": support,
                "collection_hints": [] if support["gate"] == "pass" else _collection_hints(category, entry["topic"]),
            }
        )
    evaluated.sort(
        key=lambda item: (
            0 if item["support"]["gate"] == "pass" else 1,
            -item["support"]["kept_full"],
            -item["support"]["distinct_sources"],
            -item["support"]["kept"],
            -(item["best_signal"] if item["best_signal"] is not None else -1.0),
            item["topic"],
        )
    )
    writable = [item for item in evaluated if item["support"]["gate"] == "pass"]
    blocked = [item for item in evaluated if item["support"]["gate"] != "pass"]
    refs = viral_reference_titles(category, target, limit=pool) if references else None
    return {
        "kind": "topic_support_ranking",
        "category": category,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db_path": str(target),
        "method": {
            "chain": "rag.search(MATERIAL_POOL_LIMIT) → workflow._rank_materials(topic=…) → workflow._material_gate",
            "reuse_only": True,
            "thresholds": writing_thresholds(),
            "sort_rule": "放行优先 → kept_full → distinct_sources → kept → 来源战绩分 → 主题字典序",
            "sources": list(pools),
            # 写错来源名 ⇒ 候选池会是空的,必须能在证据里看到「哪个来源没被认」
            "requested_sources": list(sources),
            "unknown_sources": unknown_sources,
            "title_seeds": "disabled(整条标题当主题会误杀又误放,见 viral_reference_titles.note)",
            "pool": pool,
            "note": "只读类目库,不调模型;门禁与阈值全部复用既有实现,本模块不新增也不放宽判定",
        },
        "candidate_sources": {
            name: {key: value for key, value in data.items() if key != "items"}
            | {"picked": len(data["items"]), "topics": [item["topic"] for item in data["items"]]}
            for name, data in pools.items()
        },
        "counters": {
            "candidates": len(evaluated),
            "writable": len(writable),
            "blocked": len(blocked),
            "viral_reference_titles": 0 if refs is None else len(refs["items"]),
        },
        "writable_topics": [item["topic"] for item in writable],
        "ranking": evaluated[:limit],
        "ranking_rest": evaluated[limit:],
        "blocked_topics": [
            {
                "topic": item["topic"],
                "critical": item["support"]["critical"],
                "kept_full": item["support"]["kept_full"],
                "distinct_sources": item["support"]["distinct_sources"],
                "collection_hints": item["collection_hints"],
            }
            for item in blocked
        ],
        "viral_reference_titles": refs,
    }


def _collection_hints(category: str, topic: str) -> list[dict]:
    """采集缺口建议:只出命令,不执行、不入队(默认干跑,红线:调度器绝不触发写作/发布)。"""
    quoted = '"' + topic.replace('"', "") + '"'
    return [
        {
            "gap": "语料缺该主题素材 ⇒ 补采集,不放宽取材门禁",
            "command": f"ttxt crawl-plan --category {category} --keyword {quoted}(干跑;确认后再加 --apply)",
        }
    ]


def evidence_path(category: str, generated_at: str, out_dir: Path | str | None = None) -> Path:
    """证据文件落 03_intelligence/topic_support/(与 keyword_feedback 同一审计目录约定)。"""
    base = Path(out_dir) if out_dir is not None else cfg.INTELLIGENCE_DIR / EVIDENCE_DIRNAME
    stamp = generated_at.replace(":", "").replace("-", "")[:15]
    return base / f"{category}-{stamp}.json"


def write_evidence(bundle: dict, path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
