# -*- coding: utf-8 -*-
"""P27 辅助裁定草案的读取、审计与护栏(不写生产数据,只碰金标准集)。

为什么要有这个脚本:本人用 ChatGPT 辅助把 61 行的三个勾打完了,但那份结果
**没有打开过任何网页,也不是本人独立复核**。直接灌进 `human_verdict` 就等于
让一份机器稿冒充人工签字,门禁(需要人工裁定 ≥50 张)会被"洗"过。
所以这里分两种模式:
  assist  把草案写进 `assist_*` 字段,`human_*` 一律不动;
  human   只有 provenance == human_signed 且 human_signature == True 才允许写 `human_*`。

取值白名单写死在这里,拼错一个勾直接报错,不做"自动纠正"。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ALLOWED_TICK1 = {"对", "不对"}
ALLOWED_TICK2 = {"能", "不能", "说不清"}
ALLOWED_TICK3 = {"同意", "改级"}
ALLOWED_TIER = set("ABCD")
KNOWN_PROVENANCE = {"chatgpt_assisted_draft", "human_signed"}
HUMAN_PROVENANCE = "human_signed"

REQUIRED_KEYS = (
    "card_id", "assist_row", "printed_row", "tick1_machine", "tick2_fact_support",
    "tick3_tier", "assist_tier_letter", "provenance", "web_pages_opened",
    "human_signature", "recorded_at",
)
# 印在纸上的机器短词 → 它到底在说什么
TOKEN_CLAIM_SUPPORTS = {"能核实"}
TOKEN_CLAIM_BLOCKED = {"对不上", "没出处"}
VERDICT_BY_TICK1 = {"对": "通过", "不对": "驳回"}

# ① 这一列到底在问什么。P27 第一版辅助稿把它读成"机器判的实对不对",15 行打成「不对」;
# 本轮(P29)更正为下面这句,并把口径写死在这里,免得第三轮又按旧意思打勾。
TICK1_SEMANTICS = (
    "①问的是:机器给这句话贴的处理标签(能核实/对不上/没出处/转述)贴不贴切;"
    "不是问这句话本身是真是假(那由 ② 和 ③ 分别回答)")
ADJUDICATOR_KINDS = {"ai", "human"}
# 裁定者类型写 ai 的行,就算把 provenance/human_signature 伪造成人工,也绝不许写 human_*
AI_ADJUDICATOR = "ai"
HUMAN_ADJUDICATOR = "human"


class DraftError(ValueError):
    """草案本身不合法(取值越界、缺字段、行号对不上)。"""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not Path(path).exists():
        raise DraftError(f"文件不存在: {path}")
    rows: list[dict[str, Any]] = []
    for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise DraftError(f"{path}:{lineno} JSON 解析失败: {exc}") from exc
    return rows


def load_draft(path: Path) -> list[dict[str, Any]]:
    rows = _read_jsonl(Path(path))
    seen: set[str] = set()
    for row in rows:
        where = f"{Path(path).name}:{row.get('card_id') or row.get('assist_row')}"
        for key in REQUIRED_KEYS:
            if key not in row:
                raise DraftError(f"{where}: 缺字段 {key}")
        if row["tick1_machine"] not in ALLOWED_TICK1:
            raise DraftError(f"{where}: ①勾取值 {row['tick1_machine']!r} 只允许 对/不对")
        if row["tick2_fact_support"] not in ALLOWED_TICK2:
            raise DraftError(f"{where}: ②勾取值 {row['tick2_fact_support']!r} 只允许 能/不能/说不清")
        if row["tick3_tier"] not in ALLOWED_TICK3:
            raise DraftError(f"{where}: ③勾取值 {row['tick3_tier']!r} 只允许 同意/改级")
        if row["assist_tier_letter"] not in ALLOWED_TIER:
            raise DraftError(f"{where}: 等级字母 {row['assist_tier_letter']!r} 只允许 A/B/C/D")
        if row["provenance"] not in KNOWN_PROVENANCE:
            raise DraftError(f"{where}: provenance {row['provenance']!r} 不在 {sorted(KNOWN_PROVENANCE)}")
        if not isinstance(row["web_pages_opened"], bool) or not isinstance(row["human_signature"], bool):
            raise DraftError(f"{where}: web_pages_opened / human_signature 必须是布尔")
        version = row.get("draft_version")
        if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
            raise DraftError(f"{where}: draft_version 必须是 ≥1 的整数,收到 {version!r}")
        adjudicator = row.get("adjudicator_type")
        if adjudicator is not None and adjudicator not in ADJUDICATOR_KINDS:
            raise DraftError(f"{where}: adjudicator_type {adjudicator!r} 只允许 ai/human")
        semantics = row.get("tick1_semantics")
        if semantics is not None and not isinstance(semantics, str):
            raise DraftError(f"{where}: tick1_semantics 必须是字符串")
        reason = row.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise DraftError(f"{where}: reason 必须是字符串")
        if isinstance(version, int) and version >= 2 and not (isinstance(reason, str) and reason.strip()):
            raise DraftError(f"{where}: 第二版起每一行都必须写明理由(reason 不能为空)")
        for flag in ("browsed_by_ai", "source_pages_checked"):
            if flag in row and not isinstance(row[flag], bool):
                raise DraftError(f"{where}: {flag} 必须是布尔")
        if row["card_id"] in seen:
            raise DraftError(f"{where}: card_id 重复")
        seen.add(row["card_id"])
    return rows


def audit(cards: list[dict[str, Any]], draft: list[dict[str, Any]]) -> dict[str, Any]:
    """把草案与卡片对着看:算术、自相矛盾的行、机器词与构造真值的对照。"""
    by_id = {c["card_id"]: c for c in cards}
    report: dict[str, Any] = {
        "total": len(draft),
        "tick1": dict(Counter(r["tick1_machine"] for r in draft)),
        "tick2": dict(Counter(r["tick2_fact_support"] for r in draft)),
        "tick3": dict(Counter(r["tick3_tier"] for r in draft)),
        "token_crosstab": {},
        "contradictions": [],
        "tick1_contradictions": [],
        "tick1_matches_token": len(draft),
        "adjudicator_types": dict(Counter(r.get("adjudicator_type", "unspecified") for r in draft)),
        "draft_versions": dict(Counter(r.get("draft_version", 1) for r in draft)),
        "tick1_semantics": TICK1_SEMANTICS,
        "tier_mismatch": [],
        "unknown_card_ids": sorted({r["card_id"] for r in draft} - set(by_id)),
    }
    # 构造真值口径:kind=positive 是"句子与原句一致"的正例,其余是故意造出来的负例
    assist_false_accept = assist_false_reject = 0
    machine_token_matches_construction = 0
    for row in draft:
        card = by_id.get(row["card_id"])
        if card is None:
            continue
        token = row.get("machine_token_printed") or ""
        tick1, tick2 = row["tick1_machine"], row["tick2_fact_support"]
        key = f"{token}|①{tick1}|②{tick2}"
        report["token_crosstab"][key] = report["token_crosstab"].get(key, 0) + 1

        if token in TOKEN_CLAIM_SUPPORTS and tick2 == "能" and tick1 == "不对":
            report["contradictions"].append({
                "card_id": row["card_id"], "printed_row": row["printed_row"],
                "assist_row": row["assist_row"], "rule": "R1",
                "why": "机器印「能核实」、②也打「能」,那①就该是「对」;现在①打了「不对」",
            })
        if token in TOKEN_CLAIM_BLOCKED and tick2 == "不能" and tick1 == "不对":
            report["contradictions"].append({
                "card_id": row["card_id"], "printed_row": row["printed_row"],
                "assist_row": row["assist_row"], "rule": "R2",
                "why": f"机器印「{token}」、②也打「不能」(原句撑不住),机器说法与②一致;①却打了「不对」",
            })
        if row["tick3_tier"] == "同意" and row["assist_tier_letter"] != card.get("source_tier"):
            report["tier_mismatch"].append({
                "card_id": row["card_id"], "letter": row["assist_tier_letter"],
                "card_tier": card.get("source_tier"),
            })

        expected = card.get("expected_by_construction")
        expected_token = {"verified": "能核实", "unverified": "对不上", "no_source": "没出处",
                          "opinion": "转述"}.get(expected, "?")
        if token == expected_token:
            machine_token_matches_construction += 1
        # R3(P29 新增,按更正后的 ① 定义):标签贴切性 = 机器贴的词与构造真值该贴的词是否一致。
        # 词一致却打「不对」⇒ 说明裁定人还在按"这句话是真是假"打①,口径又跑偏了。
        label_is_appropriate = token == expected_token
        if (row["tick1_machine"] == "对") != label_is_appropriate:
            report["tick1_matches_token"] -= 1
            report["tick1_contradictions"].append({
                "card_id": row["card_id"], "printed_row": row["printed_row"],
                "assist_row": row["assist_row"], "rule": "R3",
                "why": (f"机器贴的词「{token}」与构造真值 {expected!r} 该贴的词「{expected_token}」"
                        f"{'一致' if label_is_appropriate else '不一致'},①却打了「{row['tick1_machine']}」"
                        "——① 问的是标签贴切性,不是句子真假"),
            })
        is_positive = card.get("kind") == "positive"
        if is_positive and tick2 == "不能":
            assist_false_reject += 1
        if not is_positive and tick2 == "能":
            assist_false_accept += 1
    report["assist_false_accept"] = assist_false_accept
    report["assist_false_reject"] = assist_false_reject
    report["machine_token_matches_construction"] = machine_token_matches_construction
    return report


def diff_drafts(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, Any]:
    """比对两版草案:哪些卡片只增删、哪些字段被改动。改了什么必须看得见。"""
    old_by = {r["card_id"]: r for r in old}
    new_by = {r["card_id"]: r for r in new}
    flips: list[dict[str, Any]] = []
    for card_id, row in new_by.items():
        before = old_by.get(card_id)
        if before is None:
            continue
        for key in sorted(set(before) | set(row)):
            if before.get(key, "<缺>") != row.get(key, "<缺>"):
                flips.append({"card_id": card_id, "field": key,
                              "before": before.get(key), "after": row.get(key)})
    return {
        "old_total": len(old),
        "new_total": len(new),
        "added_ids": sorted(set(new_by) - set(old_by)),
        "removed_ids": sorted(set(old_by) - set(new_by)),
        "changed_card_ids": sorted({f["card_id"] for f in flips}),
        "flips": sorted(flips, key=lambda f: (f["card_id"], f["field"])),
    }


def human_adjudicated_count(cards: list[dict[str, Any]]) -> int:
    return sum(1 for c in cards if c.get("human_adjudicated") is True)


def merge_draft_into_cards(cards: list[dict[str, Any]], draft: list[dict[str, Any]],
                          *, mode: str) -> tuple[list[dict[str, Any]], list[str]]:
    """assist:只写 assist_*;human:只有人工签名的草案才写 human_*。"""
    if mode not in ("assist", "human"):
        return copy.deepcopy(cards), [f"未知模式 {mode!r}"]
    out = copy.deepcopy(cards)
    by_id = {c["card_id"]: c for c in out}
    errors: list[str] = []

    if mode == "human":
        ai_rows = [r["card_id"] for r in draft if r.get("adjudicator_type") == AI_ADJUDICATOR]
        if ai_rows:
            errors.append(
                f"{len(ai_rows)}/61 行的裁定者 adjudicator_type=ai:AI 自己声明不是自然人,"
                "即使 provenance/human_signature 被写成人工也拒绝写入 human_* —— 这不是签字,是冒充")
            return out, errors
        offenders = [r["card_id"] for r in draft
                     if r["provenance"] != HUMAN_PROVENANCE or r["human_signature"] is not True]
        if offenders:
            errors.append(
                f"{len(offenders)}/61 行 provenance 不是 {HUMAN_PROVENANCE} 或 human_signature 不是 True,"
                "拒绝写入 human_* —— 辅助初稿不能冒充人工独立复核")
            return out, errors

    for row in draft:
        card = by_id.get(row["card_id"])
        if card is None:
            errors.append(f"草案里的 {row['card_id']} 在卡片集里找不到,跳过")
            continue
        card["assist_verdict"] = VERDICT_BY_TICK1[row["tick1_machine"]]
        card["assist_fact_support"] = row["tick2_fact_support"]
        card["assist_confirms_tier"] = (
            "同意" if row["tick3_tier"] == "同意" else row["assist_tier_letter"])
        card["assist_tier_letter"] = row["assist_tier_letter"]
        card["assist_provenance"] = row["provenance"]
        card["assist_web_pages_opened"] = row["web_pages_opened"]
        card["assist_row"] = row["assist_row"]
        card["assist_printed_row"] = row["printed_row"]
        card["assist_recorded_at"] = row["recorded_at"]
        card["assist_draft_version"] = row.get("draft_version", 1)
        card["assist_adjudicator_type"] = row.get("adjudicator_type", "unspecified")
        card["assist_tick1_semantics"] = row.get("tick1_semantics", "未声明口径")
        card["assist_reason"] = row.get("reason")
        card["assist_browsed_by_ai"] = row.get("browsed_by_ai")
        card["assist_changed_from_v1"] = row.get("changed_from_v1")
        if mode == "human":
            card["human_adjudicated"] = True
            card["human_verdict"] = VERDICT_BY_TICK1[row["tick1_machine"]]
            card["human_fact_support"] = row["tick2_fact_support"]
            card["human_confirms_tier"] = (
                "同意" if row["tick3_tier"] == "同意" else row["assist_tier_letter"])
    return out, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="辅助裁定草案:审计(默认)或合并")
    parser.add_argument("--cards", default="10_tests/golden/golden_set_v2.jsonl")
    parser.add_argument("--draft", default="10_tests/golden/adjudications/assist_draft_chatgpt_20261116.jsonl")
    parser.add_argument("--mode", choices=("assist", "human"), default="assist")
    parser.add_argument("--out", default="", help="留空 = 只审计不写文件")
    parser.add_argument("--diff", default="", help="与另一版草案(如上一版)比对,列出被改动的字段")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    cards_path = (root / args.cards) if not Path(args.cards).is_absolute() else Path(args.cards)
    draft_path = (root / args.draft) if not Path(args.draft).is_absolute() else Path(args.draft)
    cards = _read_jsonl(cards_path)
    draft = load_draft(draft_path)
    report = audit(cards, draft)
    print(f"草案 {report['total']} 行 | ① {report['tick1']} | ② {report['tick2']} | ③ {report['tick3']}")
    print(f"① 口径:{report['tick1_semantics']}")
    print(f"裁定者类型:{json.dumps(report['adjudicator_types'], ensure_ascii=False, sort_keys=True)}"
          f" | 草案版本:{json.dumps(report['draft_versions'], ensure_ascii=False, sort_keys=True)}")
    print(f"机器词与构造真值一致: {report['machine_token_matches_construction']}/{report['total']}"
          f" | ① 与标签贴切性自洽: {report['tick1_matches_token']}/{report['total']}")
    print(f"辅助稿把负例判成「能支持」(误接受): {report['assist_false_accept']};"
          f"把正例判成「不能」(误拒): {report['assist_false_reject']}")
    print("词分布:", json.dumps(report["token_crosstab"], ensure_ascii=False, sort_keys=True))
    print(f"自相矛盾的行: {len(report['contradictions'])}")
    for item in report["contradictions"]:
        print(f"  印出第 {item['printed_row']} 行 / 本人表第 {item['assist_row']} 行 "
              f"{item['card_id']} [{item['rule']}] {item['why']}")
    print(f"① 口径跑偏的行(R3:词贴得对却打「不对」/词贴错却打「对」): {len(report['tick1_contradictions'])}")
    for item in report["tick1_contradictions"]:
        print(f"  印出第 {item['printed_row']} 行 {item['card_id']} [{item['rule']}] {item['why']}")
    if report["tier_mismatch"]:
        print(f"等级字母与卡片不符: {len(report['tier_mismatch'])}")
    if report["unknown_card_ids"]:
        print(f"卡片集里找不到的 card_id: {report['unknown_card_ids']}")
    if args.diff:
        other_path = (root / args.diff) if not Path(args.diff).is_absolute() else Path(args.diff)
        diff = diff_drafts(load_draft(other_path), draft)
        judged = [f for f in diff["flips"] if f["field"] in (
            "tick1_machine", "tick2_fact_support", "tick3_tier", "assist_tier_letter",
            "printed_row", "assist_row")]
        print(f"与 {other_path.name} 比对:新增 {len(diff['added_ids'])} / 删除 {len(diff['removed_ids'])}"
              f" / 三个勾与行号被改动 {len(judged)} 处 / 涉及卡片 {len(diff['changed_card_ids'])} 张")
        for item in judged:
            print(f"  {item['card_id']} {item['field']}: {item['before']!r} → {item['after']!r}")

    if not args.out:
        return 0
    merged, errors = merge_draft_into_cards(cards, draft, mode=args.mode)
    if errors:
        for err in errors:
            print("拒绝:", err)
        return 1
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in merged) + "\n", encoding="utf-8")
    print(f"已写 {out_path}(人工裁定 {human_adjudicated_count(merged)}/61)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
