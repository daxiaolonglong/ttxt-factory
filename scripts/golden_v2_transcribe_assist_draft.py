# -*- coding: utf-8 -*-
"""P27:把本人转来的 61 行打勾表(ChatGPT 辅助初审)逐行抄成 sidecar,并做映射核对。

这里的 TICKS 就是裁定本身,改它等于改裁定 —— 只能照抄,不许"顺手顺一遍"。
重跑会覆盖 sidecar,跑完必须再看一眼 git diff。

本人用的行号顺序是 财经 1-19 / 健康 20-36 / 科技 37-61;
印出的裁定单顺序是 科技 1-25 / 财经 26-44 / 健康 45-61。
这里**先做映射核对**(③勾的字母必须等于卡片 source_tier,否则映射不成立),核对通过才落盘。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"
OUT = ROOT / "10_tests" / "golden" / "adjudications" / "assist_draft_chatgpt_20261116.jsonl"

# (组, 本人组内行号起, 期望行数, 本人 ③ 勾的字母)
GROUPS = [("finance", 1, 19, "C"), ("health", 20, 17, "A"), ("technology", 37, 25, "C")]

# 每行: (① 对/不对, ② 能/不能/说不清)  —— 逐行照本人表格抄录,不做任何"顺一遍"
TICKS = {
    "finance": [
        ("对", "能"), ("对", "不能"), ("对", "不能"), ("对", "不能"), ("对", "不能"),
        ("不对", "不能"), ("对", "能"), ("对", "不能"), ("对", "不能"), ("对", "不能"),
        ("对", "不能"), ("不对", "不能"), ("对", "能"), ("对", "不能"), ("对", "不能"),
        ("对", "不能"), ("不对", "不能"), ("对", "不能"), ("对", "不能"),
    ],
    "health": [
        ("不对", "能"), ("不对", "不能"), ("不对", "不能"), ("不对", "不能"), ("对", "能"),
        ("对", "不能"), ("对", "不能"), ("对", "不能"), ("不对", "不能"), ("对", "能"),
        ("对", "不能"), ("对", "不能"), ("对", "不能"), ("对", "不能"), ("不对", "不能"),
        ("不对", "不能"), ("对", "不能"),
    ],
    "technology": [
        ("对", "能"), ("对", "不能"), ("对", "不能"), ("对", "不能"), ("不对", "不能"),
        ("对", "能"), ("对", "不能"), ("对", "不能"), ("对", "不能"), ("对", "不能"),
        ("不对", "不能"), ("对", "能"), ("不对", "不能"), ("对", "不能"), ("对", "不能"),
        ("对", "不能"), ("不对", "不能"), ("对", "能"), ("对", "不能"), ("对", "不能"),
        ("对", "不能"), ("对", "不能"), ("不对", "不能"), ("对", "不能"), ("对", "不能"),
    ],
}

MACHINE_ZH = {"verified": "能核实", "unverified": "对不上", "no_source": "没出处", "opinion": "转述"}
CAT_ORDER = ["technology", "finance", "health"]  # 印出的裁定单顺序


def main() -> int:
    cards = [json.loads(l) for l in CARDS.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_cat: dict[str, list[dict]] = {}
    for card in cards:
        by_cat.setdefault(card["category"], []).append(card)
    for cat in by_cat:
        by_cat[cat].sort(key=lambda c: c["card_id"])

    # 印出的裁定单行号(科技 1-25 / 财经 26-44 / 健康 45-61)
    printed_row: dict[str, int] = {}
    n = 0
    for cat in CAT_ORDER:
        for card in by_cat[cat]:
            n += 1
            printed_row[card["card_id"]] = n

    records: list[dict] = []
    mismatch: list[str] = []
    for cat, start, expected_len, letter in GROUPS:
        group = by_cat[cat]
        if len(group) != expected_len:
            print(f"组 {cat} 行数不对:期望 {expected_len},实际 {len(group)}")
            return 1
        ticks = TICKS[cat]
        if len(ticks) != expected_len:
            print(f"组 {cat} 打勾抄录条数不对:期望 {expected_len},抄到 {len(ticks)}")
            return 1
        for idx, card in enumerate(group):
            tick1, tick2 = ticks[idx]
            if card["source_tier"] != letter:
                mismatch.append(f"{card['card_id']}:本人 ③ 同意 {letter},卡片 source_tier={card['source_tier']}")
            records.append({
                "card_id": card["card_id"],
                "category": cat,
                "assist_row": start + idx,        # 本人表格里的行号
                "printed_row": printed_row[card["card_id"]],  # 印出的裁定单行号
                "tick1_machine": tick1,
                "tick2_fact_support": tick2,
                "tick3_tier": "同意",
                "assist_tier_letter": letter,
                "machine_token_printed": MACHINE_ZH.get(card["expected_by_construction"], "?"),
                "provenance": "chatgpt_assisted_draft",
                "web_pages_opened": False,
                "human_signature": False,
                "recorded_at": "2026-11-16T14:58+08:00",
            })

    if mismatch:
        print("③ 字母与卡片等级不一致(映射可能错):")
        for line in mismatch[:12]:
            print("  " + line)
        return 1

    c1 = {}
    c2 = {}
    for r in records:
        c1[r["tick1_machine"]] = c1.get(r["tick1_machine"], 0) + 1
        c2[r["tick2_fact_support"]] = c2.get(r["tick2_fact_support"], 0) + 1
    print(f"sidecar 记录 {len(records)} 条 → {OUT}")
    print("① 分布:", c1, "| ② 分布:", c2, "| ③ 同意:", sum(1 for r in records if r["tick3_tier"] == "同意"))
    print("本人陷阱卡(其行号 49)映射到:", [r["card_id"] for r in records if r["assist_row"] == 49])
    print("印出的第 13 行:", [r["card_id"] for r in records if r["printed_row"] == 13])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
