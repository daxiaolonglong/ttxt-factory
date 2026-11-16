# -*- coding: utf-8 -*-
"""P29:把**第二版**辅助裁定(15 行第①勾按正确口径重裁)录成 sidecar。

为什么要有第二版(P27 第一版的错,写死在这里免得再犯):
第一版把 ① 读成"机器判断的事实是否正确",于是 15 行打了「不对」——其中 10 行
机器贴的其实是「转述」(观点/归属路由,不宣称已被来源证实)。本轮交回的表格明确
按"**机器使用的处理标签是否贴切**"重裁,15 行全部改成「对」,并给出 5 行逐条理由。

边界(不可让):
* 交回的仍是 **AI 裁定**:裁定者 `adjudicator_type=ai`,AI 自己声明不是自然人,
  不能签真人名字。所以本脚本只写 `assist_*` 侧的 sidecar,`golden_set_v2.jsonl`
  一个字节都不改,门禁(人工裁定 0/61)照旧不过。
* 每一行都要写理由;改了什么由 `golden_v2_assist_audit.py --diff` 对着看。
* 五处理由不是走形式:脚本会拿卡里存的原文逐条核对,对不上直接报错不落盘。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import scripts.golden_v2_assist_audit as assist

ROOT = Path(__file__).resolve().parents[1]

V1_DEFAULT = "10_tests/golden/adjudications/assist_draft_chatgpt_20261116.jsonl"
CARDS_DEFAULT = "10_tests/golden/golden_set_v2.jsonl"
V2_DEFAULT = "10_tests/golden/adjudications/assist_draft_chatgpt_20261116_v2.jsonl"
DRAFT_VERSION = 2
# 构造真值 → 机器该贴的词(与 golden_v2_assist_audit.audit 里的口径一致)
TOKEN_BY_TRUTH = {"verified": "能核实", "unverified": "对不上", "no_source": "没出处",
                  "opinion": "转述"}
# 交回这份裁定的时间(东八区),写死保证可复现:同一输入必得同一文件
RECORDED_AT = "2026-11-16T16:45:00+08:00"

# 本轮交回的 15 行:打印行号 + 卡片编号 + 三个勾 + 等级字母。
# 卡片编号是最终定位依据(打印行号只是给人看的坐标),旧版表格行号一律不用。
REVISED_ROWS: list[tuple[int, str, str, str, str, str]] = [
    (5, "v2t05", "对", "不能", "同意", "C"),
    (11, "v2t11", "对", "不能", "同意", "C"),
    (13, "v2t13", "对", "不能", "同意", "C"),
    (17, "v2t17", "对", "不能", "同意", "C"),
    (23, "v2t23", "对", "不能", "同意", "C"),
    (31, "v2f06", "对", "不能", "同意", "C"),
    (37, "v2f12", "对", "不能", "同意", "C"),
    (42, "v2f17", "对", "不能", "同意", "C"),
    (45, "v2h01", "对", "能", "同意", "A"),
    (46, "v2h02", "对", "不能", "同意", "A"),
    (47, "v2h03", "对", "不能", "同意", "A"),
    (48, "v2h04", "对", "不能", "同意", "A"),
    (53, "v2h09", "对", "不能", "同意", "A"),
    (59, "v2h15", "对", "不能", "同意", "A"),
    (60, "v2h16", "对", "不能", "同意", "A"),
]
DECLARED_ANCHORS = [(printed, card_id) for printed, card_id, *_ in REVISED_ROWS]

# 五处"前后矛盾"的逐条解释(交回原话的要点),用卡里存的原文核对
EXPLANATIONS = {
    "v2h01": "机器贴的是「能核实」:WHO 原文确实写了专家小组建议将两款新的埃博拉候选疫苗纳入"
             "Tumaini 临床试验,工厂句与原文原句逐字一致 ⇒ 标签贴切(①对),②也判「能」",
    "v2t13": "机器贴的是「对不上」:原文写「开售仅3天」,工厂句改成「开售仅10天」,数字被篡改"
             " ⇒ 标签贴切(①对),②判「不能」",
    "v2h02": "机器贴的是「对不上」:原文是两个事实陈述,中间没有「导致」关系,工厂句强行添加因果"
             " ⇒ 标签贴切(①对),②判「不能」",
    "v2h03": "机器贴的是「对不上」:原文主体是世界卫生组织,不是「昆仑智能研究院」,机构张冠李戴"
             " ⇒ 标签贴切(①对),②判「不能」",
    "v2h16": "机器贴的是「对不上」:工厂句讲埃博拉疫苗,绑定的来源却是青少年身体活动报告"
             ",来源完全不相关 ⇒ 标签贴切(①对),②判「不能」",
}
RESTATE_REASON = (
    "机器贴的是「转述」(观点/归属路由),它并没有宣称这句话已被来源证实 ⇒ 按 ① 的口径"
    "(标签贴不贴切)判「对」;②仍判「不能」:来源不撑住这句话里的归属与预测内容;"
    "③等级沿用卡片既有分级(单一来源 C / 机构一手稿 A)")
UNCHANGED_REASON = (
    "本轮未改动:机器贴的词与卡片构造真值该贴的词一致,①判「对」;②③沿用上一版")
FLIP_TO_OK = {card_id for _, card_id, *_ in REVISED_ROWS}
SPECIFIC_EXPLANATION_IDS = set(EXPLANATIONS)


def _resolve(p: str | Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else ROOT / path


def verify_explanations(cards: list[dict[str, Any]]) -> list[str]:
    """把交回的解释拿卡里存的原文对着看;对不上就是解释有问题,不许落盘。"""
    by_id = {c["card_id"]: c for c in cards}
    problems: list[str] = []

    def need(card_id: str, ok: bool, what: str) -> None:
        if not ok:
            problems.append(f"{card_id}:解释与卡里存的原文对不上 —— {what}")

    c = by_id.get("v2h01") or {}
    need("v2h01", c.get("claim") == c.get("source_excerpt") and c.get("kind") == "positive",
         "工厂句应与原句逐字一致(正例)")
    c = by_id.get("v2t13") or {}
    need("v2t13", "10天" in c.get("claim", "") and "3天" in c.get("source_excerpt", "")
         and "10天" not in c.get("source_excerpt", ""), "工厂句写 10 天、原文应写 3 天")
    c = by_id.get("v2h02") or {}
    need("v2h02", "试验导致该建议意味着" in c.get("claim", "")
         and "。该建议意味着" in c.get("source_text", "")
         and "试验导致该建议" not in c.get("source_text", ""),
         "原文应是两句独立陈述,工厂句把「导致」焊在中间")
    c = by_id.get("v2h03") or {}
    need("v2h03", "昆仑智能研究院" in c.get("claim", "")
         and "昆仑智能研究院" not in c.get("source_text", "")
         and "世界卫生组织" in c.get("source_text", ""),
         "原文主体应是世界卫生组织,工厂句换成另一个机构")
    c = by_id.get("v2h16") or {}
    need("v2h16", "埃博拉" not in c.get("source_text", "") and "身体活动" in c.get("source_text", ""),
         "工厂句讲埃博拉疫苗,绑定的原文应讲的是身体活动报告")
    return problems


def write_draft(v1_path: str | Path, out_path: str | Path,
                cards_path: str | Path = CARDS_DEFAULT) -> dict[str, Any]:
    """由第一版草案 + 本轮交回的 15 行,生成第二版 sidecar(可复现)。"""
    v1 = assist.load_draft(_resolve(v1_path))
    cards = assist._read_jsonl(_resolve(cards_path))
    by_card = {c["card_id"]: c for c in cards}
    by_printed = {r["printed_row"]: r for r in v1}

    problems = verify_explanations(cards)
    if problems:
        raise assist.DraftError("交回的解释与卡里存的原文对不上,拒绝落盘:" + "; ".join(problems))

    declared = {card_id for _, card_id, *_ in REVISED_ROWS}
    disputed = {r["card_id"] for r in v1 if r["tick1_machine"] == "不对"}
    if declared != disputed:
        raise assist.DraftError(
            f"本轮声明要改的 {len(declared)} 行与上一版打「不对」的 {len(disputed)} 行不一致:"
            f"只本轮有 {sorted(declared - disputed)},只上一版有 {sorted(disputed - declared)}")

    rows: list[dict[str, Any]] = []
    flipped = 0
    for printed, card_id, tick1, tick2, tick3, letter in REVISED_ROWS:
        row = by_printed.get(printed)
        if row is None:
            raise assist.DraftError(f"上一版草案里找不到打印第 {printed} 行")
        if row["card_id"] != card_id:
            raise assist.DraftError(
                f"打印第 {printed} 行在上一版里是 {row['card_id']},本轮写的是 {card_id} —— 行号错位,拒绝")
        for field, mine, label in (("tick2_fact_support", tick2, "②"),
                                   ("tick3_tier", tick3, "③"),
                                   ("assist_tier_letter", letter, "等级字母")):
            if row[field] != mine:
                raise assist.DraftError(
                    f"{card_id}:本轮 {label}={mine!r} 与上一版 {row[field]!r} 不同;"
                    "本轮只授权改 ①,要改 ②③/等级请另开一轮并写明理由")
        flipped += 1

    for row in v1:
        card = by_card.get(row["card_id"])
        changed = row["card_id"] in declared
        new = dict(row)
        new["draft_version"] = DRAFT_VERSION
        new["tick1_semantics"] = assist.TICK1_SEMANTICS
        new["adjudicator_type"] = "ai"
        new["changed_from_v1"] = changed
        new["browsed_by_ai"] = changed
        # 上一版 web_pages_opened 记的是"本人有没有打开过网页";本轮仍是 AI 在浏览,
        # 真人一次都没打开,所以这一列保持 False,另开 browsed_by_ai 说明是谁打开的。
        new["web_pages_opened"] = False
        new["recorded_at"] = RECORDED_AT
        # 每一行(改过的和没改的)都要先过这一关:机器贴的词必须与构造真值该贴的词一致,
        # 否则 ①=「对」(标签贴切)就没有依据,必须单独裁定,不许批量沿用。
        if card is not None:
            truth = card.get("expected_by_construction")
            expected_token = TOKEN_BY_TRUTH.get(truth, "?")
            if row.get("machine_token_printed") != expected_token:
                raise assist.DraftError(
                    f"{row['card_id']}:机器贴的词 {row.get('machine_token_printed')!r} 与构造真值 "
                    f"{truth!r} 该贴的词 {expected_token!r} 不一致,①不许打「对」,请单独裁定")
        if changed:
            new["tick1_machine"] = "对"
            new["reason"] = EXPLANATIONS.get(row["card_id"], RESTATE_REASON)
        else:
            new["reason"] = UNCHANGED_REASON
        rows.append(new)

    if flipped != len(declared):
        raise assist.DraftError(f"本轮交回 {len(declared)} 行,但只有 {flipped} 行落在打印行号上")

    out = _resolve(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    report = assist.audit(cards, rows)
    return {
        "out": str(out),
        "rows": len(rows),
        "flipped_rows": sorted(declared),
        "draft_version": DRAFT_VERSION,
        "adjudicator_type": "ai",
        "tick1": report["tick1"],
        "tick2": report["tick2"],
        "tick3": report["tick3"],
        "tick1_matches_token": report["tick1_matches_token"],
        "tick1_contradictions": len(report["tick1_contradictions"]),
        "tier_mismatch": len(report["tier_mismatch"]),
        "human_adjudicated_in_cards": assist.human_adjudicated_count(cards),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成第二版辅助裁定 sidecar(只写 assist 侧)")
    parser.add_argument("--v1", default=V1_DEFAULT)
    parser.add_argument("--cards", default=CARDS_DEFAULT)
    parser.add_argument("--out", default=V2_DEFAULT)
    args = parser.parse_args(argv)
    report = write_draft(args.v1, args.out, args.cards)
    print(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True))
    print("注意:裁定者是 AI(adjudicator_type=ai),human_* 一个字节都没写;"
          f"卡片集里人工裁定仍是 {report['human_adjudicated_in_cards']}/61")
    return 0


if __name__ == "__main__":
    sys.exit(main())
