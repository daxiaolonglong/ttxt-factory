"""P13 批次1:黄金核验集 v2 度量器与质量门禁。

设计见 10_tests/golden/golden_set_v2.schema.md。要点:
- 只有 human_adjudicated=true 的卡片参与「机器 vs 人工」质量度量;
- 机器结论与「构造预期」的一致率只是规则吻合度,不得当作 L4 质量分数;
- 已人工裁定卡片 < MIN_HUMAN_CASES 时,quality_claim = NOT_AVAILABLE,
  任何「事实核验达标」的结论都不成立。

用法:
    python scripts/golden_v2_harness.py [--cards 10_tests/golden/golden_set_v2.jsonl]
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


def evaluate(cards: list[dict]) -> dict:
    adjudicated = [c for c in cards if c.get("human_adjudicated") is True]
    rows = []
    for card in cards:
        m = machine_result(card)
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

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "card_count": len(cards),
        "adjudicated_count": len(adjudicated),
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
        },
        "source_independence": {
            "tier_counts": dict(tier_counts),
            "human_confirmed_tiers": dict(adjudicated_tiers),
            "independent_share_of_adjudicated": ind_share,
        },
        "distribution": {
            "per_category_all": dict(Counter(c.get("category") for c in cards)),
            "per_category_adjudicated": dict(per_category),
            "per_kind": dict(per_kind),
            "per_fact_type": dict(Counter(c.get("fact_type") for c in cards)),
        },
        "quality_claim": "NOT_AVAILABLE" if gate_fail else "AVAILABLE",
        "gate_failures": gate_fail,
        "rows": rows,
    }


def render_report(metrics: dict, schema_errors: list[str]) -> str:
    m_h = metrics["machine_vs_human"]
    m_c = metrics["machine_vs_construction"]
    lines = [
        "# P13 批次1 黄金核验集 v2 状态(由 scripts/golden_v2_harness.py 生成)",
        "",
        f"- 生成时间:{metrics['generated_at']}",
        f"- 卡片总数(候选):{metrics['card_count']};**已人工裁定:{metrics['adjudicated_count']}**",
        f"- **质量结论:{metrics['quality_claim']}**"
        + ("" if metrics["quality_claim"] == "AVAILABLE" else " —— 门禁未通过,禁止宣称「L4 事实核验达标」"),
    ]
    if metrics["gate_failures"]:
        lines.append("- 门禁未满足项:")
        lines.extend(f"  - {item}" for item in metrics["gate_failures"])
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
        "## 3. 来源独立性",
        f"- 声明证据级分布:{metrics['source_independence']['tier_counts']}",
        f"- 人工确认证据级分布:{metrics['source_independence']['human_confirmed_tiers']}",
        f"- 已裁定卡片中 A/B 级占比:{metrics['source_independence']['independent_share_of_adjudicated']}",
        "",
        "## 4. 覆盖分布",
        f"- 类目(全部/已裁定):{metrics['distribution']['per_category_all']}"
        f" / {metrics['distribution']['per_category_adjudicated']}",
        f"- kind:{metrics['distribution']['per_kind']}",
        f"- fact_type:{metrics['distribution']['per_fact_type']}",
        "",
        "## 5. 人工裁定方法",
        "- 逐条回复 `v2xNN: 通过` 或 `v2xNN: 驳回`;需要修正证据级时回复 `v2xNN: 证据级=B`。",
        "- 「驳回」= 认为机器结论错误;只有 `human_adjudicated=true` 的卡片进入 §2 的质量口径。",
        "- 待裁定清单见同目录人工复核表(由 build_golden_set_v2.py 生成)。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="黄金核验集 v2 度量器")
    parser.add_argument("--cards", default=str(CARDS_PATH))
    parser.add_argument("--out", default=str(REPORT_PATH))
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args(argv)

    cards, errors = load_cards(Path(args.cards))
    metrics = evaluate(cards)
    print(json.dumps({k: v for k, v in metrics.items() if k != "rows"}, ensure_ascii=False, indent=2))
    if errors:
        print(f"\n模式校验错误 {len(errors)} 条:", file=sys.stderr)
        for item in errors:
            print("  -", item, file=sys.stderr)
    if not args.no_report:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_report(metrics, errors), encoding="utf-8")
        print(f"\n报告:{out}")
    if errors:
        return 1
    # 门禁未通过必须让 CI / 每周巡检能机器识别:NOT_AVAILABLE → 退出码 2
    return 0 if metrics["quality_claim"] == "AVAILABLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
