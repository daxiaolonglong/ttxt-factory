"""P13 批次1:黄金核验集 v2 度量器与质量门禁。

设计见 10_tests/golden/golden_set_v2.schema.md。要点:
- 只有 human_adjudicated=true 的卡片参与「机器 vs 人工」质量度量;
- 机器结论与「构造预期」的一致率只是规则吻合度,不得当作 L4 质量分数;
- 已人工裁定卡片 < MIN_HUMAN_CASES 时,quality_claim = NOT_AVAILABLE,
  任何「事实核验达标」的结论都不成立。

**两种口径必须同时报,不得互相冒充(2026-11-17 项目负责人口径决定)**:
- 人工口径:卡片文件里的 `human_*` 字段。目前为 0 条,`quality_claim` 只由它决定,
  退出码也只看它 —— 这样 CI / 每周巡检不会因 AI 初审而偷偷变绿。
- AI 口径:独立 sidecar `golden_set_v2_ai_adjudication.jsonl` 的 `ai_*` 字段
  (AI 辅助初审,项目负责人 2026-11-17 15:06 授权)。单独报 `quality_claim_ai`,
  并在报告里写明「AI 口径达标 ≠ 人工达标、≠ L4 通过」。
- sidecar **禁止**携带任何 `human_*` 字段,发现即报模式错误(防止 AI 结果洗成人工)。

用法:
    python scripts/golden_v2_harness.py [--cards 10_tests/golden/golden_set_v2.jsonl]
                                        [--ai-sidecar 10_tests/golden/golden_set_v2_ai_adjudication.jsonl]
                                        [--out 08_wiki/reports/P13-batch1-golden-v2-status.md]
                                        [--no-report]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ttxt.writing import verifier  # noqa: E402

CARDS_PATH = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"
AI_SIDECAR_PATH = ROOT / "10_tests" / "golden" / "golden_set_v2_ai_adjudication.jsonl"
REPORT_PATH = ROOT / "08_wiki" / "reports" / "P13-batch1-golden-v2-status.md"

MIN_HUMAN_CASES = 50          # 少于该数量不得宣称质量达标
MIN_PER_CATEGORY = 10         # 每类目有效卡片下限
MIN_INDEPENDENT_SHARE = 0.50  # A+B 级占比下限(按已裁定卡片计)
MIN_NEGATIVE_SHARE = 0.30     # 负样本占比下限
MIN_PER_KIND = 3              # 每种 kind 的卡片下限

REQUIRED_FIELDS = (
    "card_id", "category", "fact_type", "kind", "claim", "source_url", "source_publisher",
    "source_tier", "corroborating_urls", "independence_note", "source_text", "source_excerpt",
    "quote_offset", "expected_by_construction", "human_adjudicated", "human_verdict",
    "human_fact_support", "human_confirms_tier", "note",
)
CATEGORIES = {"technology", "finance", "health"}
FACT_TYPES = {"number", "date", "person", "org", "causal", "quote"}
KINDS = {
    "positive", "negative_perturbed", "negative_fabricated_causal",
    "negative_wrong_entity", "unrelated", "opinion_attributed",
}
NEGATIVE_KINDS = {
    "negative_perturbed", "negative_fabricated_causal", "negative_wrong_entity", "unrelated",
}
TIERS = {"A", "B", "C", "D"}
EXPECTED = {"verified", "unverified", "no_source", "opinion"}
VERDICTS = {"通过", "驳回"}

# ---- AI 口径(sidecar)校验常量:AI 结果只许待在 ai_* 字段里 ----
AI_REQUIRED_FIELDS = (
    "card_id", "ai_adjudicated", "ai_verdict", "ai_fact_support", "ai_confirms_tier",
    "ai_adjudicator", "ai_at", "machine_now",
)
AI_FACT_SUPPORTS = {"supported", "unsupported", "needs_more_evidence"}


def load_cards(path: Path) -> tuple[list[dict], list[str]]:
    """读取 JSONL 卡片并做模式校验,返回 (cards, errors)。"""
    if not path.exists():
        return [], [f"卡片文件不存在: {path}"]
    cards: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            card = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"第 {lineno} 行 JSON 解析失败: {exc}")
            continue
        where = card.get("card_id") or f"第 {lineno} 行"
        for field in REQUIRED_FIELDS:
            if field not in card:
                errors.append(f"{where}: 缺必填字段 {field}")
        for field, allowed in (
            ("category", CATEGORIES), ("fact_type", FACT_TYPES),
            ("kind", KINDS), ("source_tier", TIERS), ("expected_by_construction", EXPECTED),
        ):
            if card.get(field) not in allowed:
                errors.append(f"{where}: {field}={card.get(field)!r} 不在允许集合内")
        cid = str(card.get("card_id") or "")
        if cid in seen:
            errors.append(f"{where}: card_id 重复")
        seen.add(cid)
        if not isinstance(card.get("corroborating_urls"), list):
            errors.append(f"{where}: corroborating_urls 必须是列表")
        elif card.get("source_tier") == "B" and not card["corroborating_urls"]:
            errors.append(f"{where}: tier=B 必须给出至少 1 条独立来源")
        if not isinstance(card.get("human_adjudicated"), bool):
            errors.append(f"{where}: human_adjudicated 必须是布尔")
        if card.get("human_verdict") not in (None, *VERDICTS):
            errors.append(f"{where}: human_verdict 只能是 null/通过/驳回")
        text, excerpt = card.get("source_text") or "", card.get("source_excerpt") or ""
        if excerpt and excerpt not in text:
            errors.append(f"{where}: source_excerpt 不是 source_text 的逐字片段")
        if not (card.get("source_text") or "").strip():
            errors.append(f"{where}: source_text 为空,harness 无法离线复现")
        cards.append(card)
    return cards, errors


def ai_sidecar_path_for(cards_path: Path) -> Path:
    """sidecar 默认与卡片同目录、同名前缀 + `_ai_adjudication.jsonl`。

    这样 `--cards /tmp/x.jsonl` 的临时夹具不会误吃到仓库里的 61 张 AI 裁定。
    """
    if cards_path.suffix == ".jsonl":
        return cards_path.with_name(cards_path.stem + "_ai_adjudication.jsonl")
    return AI_SIDECAR_PATH


def load_ai_adjudications(path: Path, known_ids: set[str]) -> tuple[dict[str, dict], list[str]]:
    """读取 AI 辅助初审 sidecar(一行一张,按 card_id 对齐)。只读,不写任何文件。

    铁律(规则 54 补充口径):sidecar 里出现任何 `human_*` 字段就是**模式错误** ——
    AI 裁定不得洗成人工裁定;缺字段、取值越界、card_id 对不上卡片文件同样报错。
    文件不存在 → 按「AI 口径 0 条」计,不算错误(没做过初审也要能跑度量器)。
    """
    if not path.exists():
        return {}, []
    out: dict[str, dict] = {}
    errors: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"sidecar 第 {lineno} 行 JSON 解析失败:{exc}")
            continue
        cid = str(rec.get("card_id") or "")
        where = cid or f"sidecar 第 {lineno} 行"
        leaked = sorted(k for k in rec if k.startswith("human_"))
        if leaked:
            errors.append(f"{where}: sidecar 禁止携带 human_* 字段 {leaked}(AI 裁定冒充人工裁定)")
        for field in AI_REQUIRED_FIELDS:
            if field not in rec:
                errors.append(f"{where}: sidecar 缺必填字段 {field}")
        if rec.get("ai_adjudicated") is not True:
            errors.append(f"{where}: ai_adjudicated 必须为 true(AI 未裁的卡不得进 sidecar)")
        for field, allowed in (("ai_verdict", VERDICTS), ("ai_fact_support", AI_FACT_SUPPORTS),
                               ("ai_confirms_tier", TIERS), ("machine_now", EXPECTED)):
            if rec.get(field) not in allowed:
                errors.append(f"{where}: {field}={rec.get(field)!r} 不在允许集合内")
        if not str(rec.get("ai_adjudicator") or "").strip():
            errors.append(f"{where}: ai_adjudicator 不得为空(必须写明是 AI,不得留空冒充人工)")
        if not str(rec.get("ai_at") or "").strip():
            errors.append(f"{where}: ai_at 不得为空")
        if cid in out:
            errors.append(f"{where}: sidecar card_id 重复")
        if cid and cid not in known_ids:
            errors.append(f"{where}: sidecar 的 card_id 在卡片文件里不存在")
        out[cid] = rec
    return out, errors


def machine_result(card: dict) -> dict:
    """现算机器结论(① 原文匹配),不写入卡片文件。"""
    article = {
        "id": card.get("source_article_id"),
        "title": card.get("source_title") or "",
        "url": card.get("source_url") or "",
        "text": card.get("source_text") or "",
    }
    ev = verifier.verify_claim(card.get("claim") or "", article)
    quote_ok = verifier.quote_verbatim(ev.get("quote"), ev.get("quote_offset"), article["text"])
    checks = ev.get("checks") or {}
    missing = []
    for dim, payload in checks.items():
        if isinstance(payload, dict) and payload.get("missing"):
            missing.append(f"{dim}:{'|'.join(str(x) for x in payload['missing'])}")
    return {
        "credibility": ev.get("credibility"),
        "quote": ev.get("quote"),
        "quote_offset": ev.get("quote_offset"),
        "quote_verbatim_ok": quote_ok,
        "missing_dims": missing,
        "opinion_markers": checks.get("opinion_markers") or [],
    }


def evaluate(cards: list[dict], ai: dict[str, dict] | None = None) -> dict:
    ai = ai or {}
    adjudicated = [c for c in cards if c.get("human_adjudicated") is True]
    rows = []
    for card in cards:
        m = machine_result(card)
        rec = ai.get(card.get("card_id")) or {}
        rows.append({
            "card_id": card.get("card_id"),
            "kind": card.get("kind"),
            "fact_type": card.get("fact_type"),
            "category": card.get("category"),
            "tier": card.get("source_tier"),
            "machine": m["credibility"],
            "expected": card.get("expected_by_construction"),
            "adjudicated": card.get("human_adjudicated") is True,
            "verdict": card.get("human_verdict"),
            "agree_construction": m["credibility"] == card.get("expected_by_construction"),
            # ---- AI 口径(只从 sidecar 取,卡片里的 human_* 一律不动) ----
            "ai_adjudicated": bool(rec.get("ai_adjudicated")),
            "ai_verdict": rec.get("ai_verdict"),
            "ai_verdict_current": rec.get("ai_verdict_current") or rec.get("ai_verdict"),
            "ai_basis_stale": bool(rec.get("ai_basis_stale")),
            "ai_fact_support": rec.get("ai_fact_support"),
            "ai_confirms_tier": rec.get("ai_confirms_tier"),
            "machine_at_ai_adjudication": rec.get("machine_at_adjudication"),
            "machine_recorded_in_sidecar": rec.get("machine_now"),
        })

    def subset(rows_, kinds=None, machines=None, verdict=None):
        out = rows_
        if kinds is not None:
            out = [r for r in out if r["kind"] in kinds]
        if machines is not None:
            out = [r for r in out if r["machine"] in machines]
        if verdict is not None:
            out = [r for r in out if r["verdict"] == verdict]
        return out

    adj_rows = [r for r in rows if r["adjudicated"]]
    agree_construction = sum(1 for r in rows if r["agree_construction"])
    human_agree = len(subset(adj_rows, verdict="通过"))
    human_reject = len(subset(adj_rows, verdict="驳回"))
    adj_neg = subset(adj_rows, kinds=NEGATIVE_KINDS)
    neg_false_accept = len(subset(adj_neg, machines={"verified"}, verdict="通过"))
    adj_pos = subset(adj_rows, kinds={"positive"})
    pos_false_reject = len(subset(adj_pos, machines={"unverified", "no_source"}, verdict="通过"))

    def share(part, whole):
        return round(part / whole, 3) if whole else None

    tier_counts = Counter(c.get("source_tier") for c in cards)
    # T-02:把「独立性」拆成可解释的读数。A 级按 schema 允许 corroborating_urls 为空(一手出处即可),
    # 把「61/61 为空」当成独立性不达标是误读;真正缺的是 B 级的独立第二来源。
    corroborating_present = sum(1 for c in cards if c.get("corroborating_urls"))
    b_missing_corroborating = sum(
        1 for c in cards if c.get("source_tier") == "B" and not c.get("corroborating_urls"))
    # 替代佐证口径:只在"客观可核锚点"的卡片里算一手出处(A)占比。它不参与 A+B>=50% 门禁,
    # 也不改写卡片数据 —— 只回答"在现有来源结构下,可核事实里有多少拿得到一手出处"。
    anchor_types = ("number", "date", "person", "org")
    anchor_cards = [c for c in cards if c.get("fact_type") in anchor_types]
    anchor_first_hand = sum(1 for c in anchor_cards if c.get("source_tier") == "A")
    adjudicated_tiers = Counter(c.get("human_confirms_tier") or c.get("source_tier") for c in adjudicated)
    independent_human = sum(
        1 for c in adjudicated if (c.get("human_confirms_tier") or c.get("source_tier")) in {"A", "B"}
    )
    per_category = Counter(c.get("category") for c in adjudicated)
    per_kind = Counter(c.get("kind") for c in cards)

    gate_fail = []
    if len(adjudicated) < MIN_HUMAN_CASES:
        gate_fail.append(f"已人工裁定 {len(adjudicated)} < {MIN_HUMAN_CASES}")
    short_cats = {k: v for k, v in per_category.items() if v < MIN_PER_CATEGORY}
    missing_cats = sorted(CATEGORIES - set(per_category))
    if short_cats or missing_cats:
        gate_fail.append(
            f"类目有效卡片不足: {short_cats or {}} 缺类目 {missing_cats}(每类目需 ≥{MIN_PER_CATEGORY})"
        )
    ind_share = share(independent_human, len(adjudicated))
    if ind_share is None or ind_share < MIN_INDEPENDENT_SHARE:
        gate_fail.append(f"A/B 级独立证据占比 {ind_share} < {MIN_INDEPENDENT_SHARE}")
    neg_share = share(len(subset(rows, kinds=NEGATIVE_KINDS)), len(cards))
    if neg_share is None or neg_share < MIN_NEGATIVE_SHARE:
        gate_fail.append(f"负样本占比 {neg_share} < {MIN_NEGATIVE_SHARE}")
    thin_kinds = {k: v for k, v in per_kind.items() if v < MIN_PER_KIND}
    if thin_kinds:
        gate_fail.append(f"kind 覆盖不足: {thin_kinds}(每种需 ≥{MIN_PER_KIND})")

    # ---- AI 口径:与人工口径并列计算,单独一套门禁,绝不合并进上面 ----
    ai_rows = [r for r in rows if r["ai_adjudicated"]]
    ai_verdict_counts = Counter(r["ai_verdict"] for r in ai_rows)
    ai_current_counts = Counter(r["ai_verdict_current"] for r in ai_rows)
    ai_drift = [
        r["card_id"] for r in ai_rows
        if r["machine_recorded_in_sidecar"] and r["machine_recorded_in_sidecar"] != r["machine"]
    ]
    ai_neg = [r for r in ai_rows if r["kind"] in NEGATIVE_KINDS]
    ai_pos = [r for r in ai_rows if r["kind"] == "positive"]
    ai_false_accept = sum(
        1 for r in ai_neg if r["machine"] == "verified" and r["ai_verdict_current"] == "通过")
    ai_false_reject = sum(
        1 for r in ai_pos
        if r["machine"] in {"unverified", "no_source"} and r["ai_verdict_current"] == "通过")
    ai_opinion_cards = [r for r in ai_rows if r["kind"] == "opinion_attributed"]
    ai_opinion_rejected = sum(
        1 for r in ai_opinion_cards if r["machine"] == "opinion" and r["ai_verdict_current"] == "驳回")
    ai_per_category = Counter(r["category"] for r in ai_rows)
    ai_tier_counts = Counter(r["ai_confirms_tier"] for r in ai_rows)
    ai_independent = sum(1 for r in ai_rows if r["ai_confirms_tier"] in {"A", "B"})
    ai_ind_share = share(ai_independent, len(ai_rows))

    ai_gate = []
    if not ai_rows:
        ai_gate.append("AI 口径 0 条:未找到 sidecar 或尚未做 AI 初审")
    if len(ai_rows) < MIN_HUMAN_CASES:
        ai_gate.append(f"已 AI 初审 {len(ai_rows)} < {MIN_HUMAN_CASES}")
    ai_short = {k: v for k, v in ai_per_category.items() if v < MIN_PER_CATEGORY}
    ai_missing = sorted(CATEGORIES - set(ai_per_category))
    if ai_short or ai_missing:
        ai_gate.append(
            f"AI 口径类目覆盖不足: {ai_short or {}} 缺类目 {ai_missing}(每类目需 ≥{MIN_PER_CATEGORY})"
        )
    if ai_rows and (ai_ind_share is None or ai_ind_share < MIN_INDEPENDENT_SHARE):
        ai_gate.append(f"AI 确认的 A/B 级独立证据占比 {ai_ind_share} < {MIN_INDEPENDENT_SHARE}")
    if ai_drift:
        ai_gate.append(
            f"sidecar 记录的机器标签与当前机器结论不一致 {len(ai_drift)} 张:{sorted(ai_drift)[:10]}"
            "(机器结论变了 ⇒ AI 判据已过期,必须重裁,不得沿用)"
        )

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "card_count": len(cards),
        # 人工口径(唯一决定 quality_claim 与退出码的口径)
        "adjudicated_count": len(adjudicated),
        "human_adjudicated_count": len(adjudicated),
        # AI 口径(辅助初审,不得当人工用)
        "ai_adjudicated_count": len(ai_rows),
        "caliber_note": (
            "human_adjudicated_count = 真人逐张裁定;ai_adjudicated_count = AI 辅助初审。"
            "两个口径分开报数:AI 数量再多也不构成人工裁定,AI 口径达标 ≠ 人工达标 ≠ L4 通过。"
        ),
        "machine_vs_construction": {
            "agreement": agree_construction,
            "rate": share(agree_construction, len(rows)),
            "meaning": "机器结论与构造预期一致率 —— 仅规则吻合度,不是质量分数,不得用于 L4 验收",
        },
        "machine_vs_human": {
            "adjudicated": len(adjudicated),
            "agree_通过": human_agree,
            "reject_驳回": human_reject,
            "accuracy": share(human_agree, human_agree + human_reject),
            "negative_adjudicated": len(adj_neg),
            "negative_false_accept": neg_false_accept,
            "false_accept_rate": share(neg_false_accept, len(adj_neg)),
            "positive_adjudicated": len(adj_pos),
            "positive_false_reject": pos_false_reject,
            "false_reject_rate": share(pos_false_reject, len(adj_pos)),
            "meaning": "**唯一可宣称的质量口径**,只统计真人逐张裁定的卡片",
        },
        "machine_vs_ai": {
            "adjudicated": len(ai_rows),
            "adjudicator": sorted({str((ai.get(r['card_id']) or {}).get('ai_adjudicator') or '') for r in ai_rows}) or None,
            "verdict_as_recorded": dict(ai_verdict_counts),
            "verdict_on_current_machine_label": dict(ai_current_counts),
            "basis_stale_cards": sorted(r["card_id"] for r in ai_rows if r["ai_basis_stale"]),
            "machine_label_drift": sorted(ai_drift),
            "agree_通过_current": ai_current_counts.get("通过", 0),
            "reject_驳回_current": ai_current_counts.get("驳回", 0),
            "accuracy": share(ai_current_counts.get("通过", 0), len(ai_rows)),
            "negative_adjudicated": len(ai_neg),
            "negative_false_accept": ai_false_accept,
            "false_accept_rate": share(ai_false_accept, len(ai_neg)),
            "positive_adjudicated": len(ai_pos),
            "positive_false_reject": ai_false_reject,
            "false_reject_rate": share(ai_false_reject, len(ai_pos)),
            "opinion_routed_cards": len(ai_opinion_cards),
            "opinion_routing_rejected_by_ai": ai_opinion_rejected,
            "fact_support": dict(Counter(r["ai_fact_support"] for r in ai_rows)),
            "meaning": (
                "AI 辅助初审口径:只用于定位机器的可疑误判与回归样本,"
                "**不是质量分数、不是人工裁定、不得用于 L4 验收**"
            ),
        },
        "source_independence": {
            "tier_counts": dict(tier_counts),
            "human_confirmed_tiers": dict(adjudicated_tiers),
            "ai_confirmed_tiers": dict(ai_tier_counts),
            "independent_share_of_human_adjudicated": ind_share,
            "independent_share_of_ai_adjudicated": ai_ind_share,
            # T-02 拆分读数:一手(A)/跨来源(B)/单一(C)/不可核(D)分开报,避免把 A 的空
            # corroborating_urls 误读成"独立性不达标"。
            "first_hand_A": tier_counts.get("A", 0),
            "cross_source_B": tier_counts.get("B", 0),
            "single_source_C": tier_counts.get("C", 0),
            "unverifiable_D": tier_counts.get("D", 0),
            "cards_with_corroborating_urls": corroborating_present,
            "b_tier_missing_corroborating": b_missing_corroborating,
            "a_tier_does_not_require_corroborating": True,
            "primary_anchor_share": {
                "fact_types": list(anchor_types),
                "cards": len(anchor_cards),
                "first_hand_A": anchor_first_hand,
                "share": share(anchor_first_hand, len(anchor_cards)),
                "meaning": (
                    "替代佐证口径:只在客观可核锚点(number/date/person/org)的卡片里算一手出处占比。"
                    "不参与 A+B>=50% 门禁,B 级仍必须由独立第二来源构成;不改写卡片、不硬凑数据"
                ),
            },
            "gate_note": (
                "A 级按 schema 允许 corroborating_urls 为空(一手出处即可),B 级必须非空;"
                "要过 A+B>=50% 只能补真正的独立第二来源,不得把 A 的空白当缺陷、不得改门禁分母"
            ),
        },
        "distribution": {
            "per_category_all": dict(Counter(c.get("category") for c in cards)),
            "per_category_human_adjudicated": dict(per_category),
            "per_category_ai_adjudicated": dict(ai_per_category),
            "per_kind": dict(per_kind),
            "per_fact_type": dict(Counter(c.get("fact_type") for c in cards)),
        },
        "quality_claim": "NOT_AVAILABLE" if gate_fail else "AVAILABLE",
        "quality_claim_basis": "只由人工口径(gate_failures)决定;AI 口径不影响退出码,防止 AI 初审让 CI 偷偷变绿",
        "gate_failures": gate_fail,
        "quality_claim_ai": "NOT_AVAILABLE" if ai_gate else "AVAILABLE",
        "ai_gate_failures": ai_gate,
        "rows": rows,
    }


def render_report(metrics: dict, schema_errors: list[str]) -> str:
    m_h = metrics["machine_vs_human"]
    m_a = metrics["machine_vs_ai"]
    m_c = metrics["machine_vs_construction"]
    lines = [
        "# P13 批次1 黄金核验集 v2 状态(由 scripts/golden_v2_harness.py 生成)",
        "",
        f"- 生成时间:{metrics['generated_at']}",
        f"- 卡片总数(候选):{metrics['card_count']}",
        f"- **已人工裁定:{metrics['human_adjudicated_count']}**;"
        f"已 AI 辅助初审:{metrics['ai_adjudicated_count']}",
        f"- {metrics['caliber_note']}",
        f"- **质量结论(人工口径,唯一决定退出码):{metrics['quality_claim']}**"
        + ("" if metrics["quality_claim"] == "AVAILABLE" else " —— 门禁未通过,禁止宣称「L4 事实核验达标」"),
        f"- 质量结论(AI 口径,仅供参考):{metrics['quality_claim_ai']}",
    ]
    if metrics["gate_failures"]:
        lines.append("- 人工口径门禁未满足项:")
        lines.extend(f"  - {item}" for item in metrics["gate_failures"])
    if metrics["ai_gate_failures"]:
        lines.append("- AI 口径门禁未满足项:")
        lines.extend(f"  - {item}" for item in metrics["ai_gate_failures"])
    if schema_errors:
        lines.append(f"- ⚠️ 模式校验错误 {len(schema_errors)} 条(这些卡片不计入任何结论):")
        lines.extend(f"  - {item}" for item in schema_errors[:40])
    lines += [
        "",
        "## 1. 规则吻合度(不是质量分数)",
        f"- 机器结论与构造预期一致:{m_c['agreement']}/{metrics['card_count']}"
        f"(rate={m_c['rate']}) —— {m_c['meaning']}",
        "",
        "## 2. 机器 vs 人工(唯一的质量口径)",
        f"- 已人工裁定:{m_h['adjudicated']};通过 {m_h['agree_通过']} / 驳回 {m_h['reject_驳回']}"
        f";一致率 {m_h['accuracy']}",
        f"- 负样本(已裁定 {m_h['negative_adjudicated']}):误判 verified {m_h['negative_false_accept']}"
        f";**false_accept_rate = {m_h['false_accept_rate']}**",
        f"- 正样本(已裁定 {m_h['positive_adjudicated']}):误判 unverified/no_source"
        f" {m_h['positive_false_reject']};false_reject_rate = {m_h['false_reject_rate']}",
        "",
        "## 2b. 机器 vs AI 辅助初审(参考口径,不是质量分数)",
        f"- 已 AI 初审:{m_a['adjudicated']};裁定者 {m_a['adjudicator']}",
        f"- 按纸面标签记的 ①:{m_a['verdict_as_recorded']};"
        f"按今日机器标签重述:{m_a['verdict_on_current_machine_label']}",
        f"- 判据已过期(纸面标签≠今日标签)的卡片:{m_a['basis_stale_cards'] or '无'}",
        f"- sidecar 记录的机器标签与当前不一致:{m_a['machine_label_drift'] or '无'}",
        f"- 负样本(已 AI 初审 {m_a['negative_adjudicated']}):机器 verified 而 AI 认可"
        f" {m_a['negative_false_accept']};false_accept_rate = {m_a['false_accept_rate']}",
        f"- 正样本(已 AI 初审 {m_a['positive_adjudicated']}):机器对不上/没出处 而 AI 认可"
        f" {m_a['positive_false_reject']};false_reject_rate = {m_a['false_reject_rate']}",
        f"- 走「转述」通道的卡片 {m_a['opinion_routed_cards']} 张,AI 判机器贴得不贴切"
        f" {m_a['opinion_routing_rejected_by_ai']} 张(T-15 的线索)",
        f"- ② 原句支撑分布:{m_a['fact_support']}",
        f"- {m_a['meaning']}",
        "",
        "## 3. 来源独立性",
        f"- 声明证据级分布:{metrics['source_independence']['tier_counts']}",
        f"- 人工确认证据级分布:{metrics['source_independence']['human_confirmed_tiers']};"
        f"AI 确认(AI 口径):{metrics['source_independence']['ai_confirmed_tiers']}",
        f"- 已人工裁定卡片中 A/B 级占比:{metrics['source_independence']['independent_share_of_human_adjudicated']};"
        f"AI 口径:{metrics['source_independence']['independent_share_of_ai_adjudicated']}",
        "",
        "## 4. 覆盖分布",
        f"- 类目(全部/已人工裁定/已 AI 初审):{metrics['distribution']['per_category_all']}"
        f" / {metrics['distribution']['per_category_human_adjudicated']}"
        f" / {metrics['distribution']['per_category_ai_adjudicated']}",
        f"- kind:{metrics['distribution']['per_kind']}",
        f"- fact_type:{metrics['distribution']['per_fact_type']}",
        "",
        "## 5. 人工裁定方法",
        "- 逐条回复 `v2xNN: 通过` 或 `v2xNN: 驳回`;需要修正证据级时回复 `v2xNN: 证据级=B`。",
        "- 「驳回」= 认为机器结论错误;只有 `human_adjudicated=true` 的卡片进入 §2 的质量口径。",
        "- AI 辅助初审结果只进 sidecar(`*_ai_adjudication.jsonl`)的 `ai_*` 字段,"
        "**永远不得**写进卡片文件的 `human_*` 字段。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="黄金核验集 v2 度量器")
    parser.add_argument("--cards", default=str(CARDS_PATH))
    parser.add_argument("--ai-sidecar", default=None,
                        help="AI 辅助初审 sidecar;默认取卡片同名 *_ai_adjudication.jsonl")
    parser.add_argument("--out", default=str(REPORT_PATH))
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args(argv)

    cards_path = Path(args.cards)
    cards, errors = load_cards(cards_path)
    sidecar_path = Path(args.ai_sidecar) if args.ai_sidecar else ai_sidecar_path_for(cards_path)
    ai, ai_errors = load_ai_adjudications(sidecar_path, {c.get("card_id") for c in cards})
    metrics = evaluate(cards, ai)
    metrics["ai_sidecar_path"] = str(sidecar_path)
    print(json.dumps({k: v for k, v in metrics.items() if k != "rows"}, ensure_ascii=False, indent=2))
    if errors:
        print(f"\n模式校验错误 {len(errors)} 条:", file=sys.stderr)
        for item in errors:
            print("  -", item, file=sys.stderr)
    if ai_errors:
        print(f"\nAI sidecar 校验错误 {len(ai_errors)} 条({sidecar_path}):", file=sys.stderr)
        for item in ai_errors:
            print("  -", item, file=sys.stderr)
    if not args.no_report:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_report(metrics, errors + ai_errors), encoding="utf-8")
        print(f"\n报告:{out}")
    if errors or ai_errors:
        return 1
    # 人工口径门禁未通过必须让 CI / 每周巡检能机器识别:NOT_AVAILABLE → 退出码 2。
    # AI 口径只报数,不参与退出码 —— 否则 AI 初审一做,CI 就自己变绿了。
    return 0 if metrics["quality_claim"] == "AVAILABLE" else 2


def _force_utf8_streams() -> None:
    """门禁结论不得被控制台代码页吃掉。

    实测(2026-11-17):测试用 `subprocess`(无 `-X utf8`)调本脚本时,stdout 落到 GBK,
    `print` 判据文案里的 `⇒` 直接 `UnicodeEncodeError`,退出码从 2 变成 1 —— 门禁的真实
    结论被编码问题替换掉了。脚本自己保证 UTF-8,调用方加不加 `-X utf8` 都必须给出同一结论。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    _force_utf8_streams()
    raise SystemExit(main())
