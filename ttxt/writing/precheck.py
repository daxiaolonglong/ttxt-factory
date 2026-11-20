"""R52:选题预筛 —— 写作队列之前的新增过滤层(不改任何既有判定)。

依据 R50 归因:54 条 critical 锚点里 59.3% 在绑定来源全文里根本找不到(模型脑补/绑错文章),
这类选题没有可绑定的一手材料,无论怎么写都过不了事实门禁。本层在调用模型**之前**,
在既有材料池上量化「可绑定一手锚点」,把注定了过不了的选题挡在写作队列外。

复用,不另写一套判定:
- 取材链与写作完全同一条:``rag.search(limit=MATERIAL_POOL_LIMIT)`` + ``workflow._rank_materials``
  (取样规则、排序、选材零改动,预筛只是读它的输出);
- 锚点抽取与归一化全部走 ``verifier.extract_anchors``/``norm``/``_anchor_in``/``_date_in``/
  ``is_anaphoric_org``,并沿用 verifier 的硬门禁口径:纯中文数量词不计(建议项)、
  泛指机构不计(建议项)——预筛数锚点的尺子和门禁数锚点的尺子必须同一把;
- 独立性口径沿用取材门禁的 ``workflow._independence_key``(作者优先,缺作者退回域名)。

可解释性:每个选题输出分维度锚点清单(数字/日期/人物/机构)与逐篇材料的标题锚点定位
情况,被挡时能一句话说清「哪类锚点缺到什么程度」,不是黑箱分数。

指标(先于回溯声明,防挑数):
- ``first_hand``:所选材料全文中可定位的硬锚点去重候选数(分维度);
- ``cross_verified``:同一锚点在 **≥2 个独立来源**的全文里都能定位到 —— R50 最大失败形状
  是「锚点在绑定来源找不到」,跨独立来源都找得到的锚点最难绑错,
  故 **主指标 score = cross_verified**;
- 判定:score < threshold ⇒ 不入写作队列,记 ``rejected_no_material`` + 缺锚点说明;
  阈值来自 R50 历史数据回溯(scripts/r52_precheck_backtest.py),不是拍脑袋。

红线:本层不做门禁判定、不引用不修改任何既有阈值(取材门禁 min_full/min_sources、0.34、
±1 窗口、fact_checkability…);它只在写作队列入口前多挡一刀,既有判定一字不动。
"""

from __future__ import annotations

from pathlib import Path

from ttxt.analysis import rag
from ttxt.crawler import cleaner, store
from ttxt.writing import verifier, workflow

DIMS = ("numbers", "dates", "persons", "orgs")


def _ascii_digit_number(anchor: str) -> bool:
    """与 verifier.verify_claim 同一口径:纯中文数量词是建议项,不进硬计数。"""
    return any(ch.isdigit() and ch.isascii() for ch in verifier.norm(anchor))


def hard_anchor_set(text: str) -> dict[str, list[str]]:
    """一条正文的可绑定硬锚点(分维度,抽取侧去重),沿用 verifier 的排除口径。"""
    anchors = verifier.extract_anchors(text)
    out: dict[str, list[str]] = {}
    for dim in DIMS:
        kept = []
        for item in anchors[dim]:
            if dim == "numbers" and not _ascii_digit_number(item):
                continue
            if dim == "orgs" and verifier.is_anaphoric_org(item):
                continue
            kept.append(verifier.norm(item))
        out[dim] = kept
    return out


def found_in(dim: str, norm_anchor: str, norm_text: str) -> bool:
    """锚点能否在正文定位 —— 用 verifier 的包含判定(数字带边界、日期按分量)。"""
    if dim == "dates":
        return verifier._date_in(norm_anchor, norm_text)
    return verifier._anchor_in(norm_anchor, norm_text)


def precheck_topic(category: str, topic: str, db_path: Path | str | None = None) -> dict:
    """对候选选题在既有材料池上计算可绑定一手锚点(只读,不调模型、不写库)。

    与写作同一条取材链(rag.search → _rank_materials),材料选择/排序零改动;
    预筛只统计所选材料全文的锚点可定位性与跨独立来源的印证数。
    """
    db_path = store.articles_db_path(category) if db_path is None else Path(db_path)
    matches = rag.search(category, topic, limit=workflow.MATERIAL_POOL_LIMIT, db_path=db_path)
    materials, stats = workflow._rank_materials(matches, db_path)

    full = cleaner.COMPLETENESS_FULL
    meta_by_id = {int(s["id"]): s for s in stats.get("scores", [])}

    per_material: list[dict] = []
    # 每篇所选材料的正文(归一)与独立来源键;cross 统计只在全文文章之间做,
    # 短稿(RSS 摘要)撑不起引句,和取材门禁同一立场。
    full_bodies: list[tuple[str, str]] = []  # (source_key, norm_text)
    for m in materials:
        text = m.get("text") or ""
        meta = meta_by_id.get(int(m["id"]), {})
        source_key = workflow._independence_key(meta.get("author", ""), meta.get("host", ""))[1]
        norm_body = verifier.norm(text)
        anchors = hard_anchor_set(text)
        title_anchors = hard_anchor_set(m.get("title") or "")
        entry = {
            "id": m["id"],
            "title": (m.get("title") or "")[:60],
            "completeness": meta.get("completeness"),
            "source_key": source_key,
            "anchor_counts": {dim: len(set(anchors[dim])) for dim in DIMS},
            "title_anchor_check": {
                dim: {
                    "found": [a for a in dict.fromkeys(title_anchors[dim])
                              if found_in(dim, a, norm_body)],
                    "missing": [a for a in dict.fromkeys(title_anchors[dim])
                                if not found_in(dim, a, norm_body)],
                }
                for dim in DIMS
            },
        }
        per_material.append(entry)
        if meta.get("completeness") == full and source_key:
            full_bodies.append((source_key, norm_body))

    distinct_keys = {key for key, _ in full_bodies}
    first_hand: dict[str, list[str]] = {dim: [] for dim in DIMS}
    cross: dict[str, list[str]] = {dim: [] for dim in DIMS}
    anchors_first_hand: dict[str, dict[str, int]] = {dim: {} for dim in DIMS}
    anchors_cross: dict[str, dict[str, int]] = {dim: {} for dim in DIMS}

    for dim in DIMS:
        candidates: list[str] = []
        seen: set[str] = set()
        # 候选锚点 = 任一篇所选全文文章抽到的硬锚点(材料 ≤5 篇,重扫代价可忽略)
        for m in materials:
            meta = meta_by_id.get(int(m["id"]), {})
            if meta.get("completeness") != full:
                continue
            for anchor in hard_anchor_set(m.get("text") or "").get(dim, []):
                if anchor not in seen:
                    seen.add(anchor)
                    candidates.append(anchor)
        for anchor in candidates:
            holders = {key for key, body in full_bodies if found_in(dim, anchor, body)}
            anchors_first_hand[dim][anchor] = len(holders)
            if holders:
                first_hand[dim].append(anchor)
            if len(holders) >= 2:
                anchors_cross[dim][anchor] = len(holders)
                cross[dim].append(anchor)

    first_hand_total = sum(len(v) for v in first_hand.values())
    cross_total = sum(len(v) for v in cross.values())

    # 可解释拒因:哪个维度最薄(候选少/零印证)
    thin = [
        f"{dim} 仅 {len(first_hand[dim])} 个可定位锚点(跨源印证 {len(cross[dim])})"
        for dim in DIMS
        if len(cross[dim]) == 0
    ]

    return {
        "category": category,
        "topic": topic,
        "score": cross_total,  # 主指标(先声明):跨独立来源印证的一手锚点数
        "first_hand_total": first_hand_total,
        "cross_verified_total": cross_total,
        "first_hand_by_dim": {dim: len(first_hand[dim]) for dim in DIMS},
        "cross_by_dim": {dim: len(cross[dim]) for dim in DIMS},
        "anchors_first_hand": anchors_first_hand,
        "anchors_cross": anchors_cross,
        "full_bodies_selected": len(full_bodies),
        "distinct_source_keys": len(distinct_keys),
        "material_stats": stats,
        "per_material": per_material,
        "thin_dims": thin,
    }


def explain_rejection(pre: dict, threshold: int) -> str:
    """一句话拒因(可解释):缺哪类锚点、被算作缺失的具体锚点示例。"""
    parts = []
    for dim in DIMS:
        missing_here = pre["anchors_first_hand"].get(dim, {})
        thin = [a for a, n in missing_here.items() if n < 2]
        if len(pre["cross_by_dim"][dim]) == 0:
            sample = "、".join(list(missing_here)[:5]) if missing_here else "(一个都没定位到)"
            parts.append(f"{dim}:可定位 {len(missing_here)} 个但跨独立来源 0 印证,例:{sample}")
    reason = (
        f"可绑定一手锚点不足:跨独立来源(≥2)印证锚点 {pre['cross_verified_total']} 个 < 阈值 {threshold}"
        f"(分维度 数字{pre['cross_by_dim']['numbers']}/日期{pre['cross_by_dim']['dates']}"
        f"/人物{pre['cross_by_dim']['persons']}/机构{pre['cross_by_dim']['orgs']})"
    )
    if parts:
        reason += "。完全缺跨源印证的维度:" + ";".join(parts)
    return reason
