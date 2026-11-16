# -*- coding: utf-8 -*-
"""P27:ChatGPT 辅助初审裁定草案的录入与护栏(先红后绿)。

要守住的底线:
1. 取值只能是对/不对、能/不能/说不清、同意/改级,写错一个字就报错;
2. **AI 辅助初稿绝不能写成人工裁定** —— human_adjudicated / human_verdict 必须保持原样,
   否则门禁(人工裁定 0/61)会被一份机器稿"洗"过去;
3. 行号映射要能核对:本人表格行号 ≠ 印出的裁定单行号,陷阱卡必须落在同一张卡片上。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.golden_v2_assist_audit as assist

ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"
DRAFT_PATH = ROOT / "10_tests" / "golden" / "adjudications" / "assist_draft_chatgpt_20261116.jsonl"


def _cards() -> list[dict]:
    return [json.loads(l) for l in CARDS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_sidecar_has_61_rows_and_only_allowed_values():
    draft = assist.load_draft(DRAFT_PATH)
    assert len(draft) == 61
    assert len({r["card_id"] for r in draft}) == 61
    for r in draft:
        assert r["tick1_machine"] in assist.ALLOWED_TICK1
        assert r["tick2_fact_support"] in assist.ALLOWED_TICK2
        assert r["tick3_tier"] in assist.ALLOWED_TICK3
        assert r["assist_tier_letter"] in set("ABCD")
    assert {r["provenance"] for r in draft} == {"chatgpt_assisted_draft"}
    assert all(r["human_signature"] is False and r["web_pages_opened"] is False for r in draft)


def test_row_number_mapping_matches_printed_sheet():
    draft = assist.load_draft(DRAFT_PATH)
    by_assist = {r["assist_row"]: r for r in draft}
    # 本人表格里「第 49 行」= 印出裁定单的第 13 行 = 酷派 10 天/3 天陷阱卡
    assert by_assist[49]["card_id"] == "v2t13"
    assert by_assist[49]["printed_row"] == 13
    assert sorted(r["printed_row"] for r in draft) == list(range(1, 62))


def test_load_draft_rejects_bad_enum(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({
        "card_id": "v2t13", "category": "technology", "assist_row": 49, "printed_row": 13,
        "tick1_machine": "大概对", "tick2_fact_support": "能", "tick3_tier": "同意",
        "assist_tier_letter": "C", "provenance": "chatgpt_assisted_draft",
        "web_pages_opened": False, "human_signature": False, "recorded_at": "x",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(assist.DraftError):
        assist.load_draft(bad)


def test_draft_totals_are_exactly_these():
    """本人汇总说 47 对/14 不对、9 能/52 不能;逐行表实际是 46/15 与 10/51。
    这条测试钉住逐行表的算术,以后谁改一个勾都会被发现。"""
    report = assist.audit(_cards(), assist.load_draft(DRAFT_PATH))
    assert report["tick1"] == {"对": 46, "不对": 15}
    assert report["tick2"] == {"能": 10, "不能": 51}
    assert report["tick3"] == {"同意": 61}
    assert report["total"] == 61


def test_contradiction_rows_are_flagged():
    report = assist.audit(_cards(), assist.load_draft(DRAFT_PATH))
    ids = {row["card_id"] for row in report["contradictions"]}
    # v2h01:印的是「能核实」,本人 ② 打「能」却把 ① 打「不对」——自相矛盾,要人复核
    assert "v2h01" in ids


def test_assist_mode_never_touches_human_fields():
    cards = _cards()
    merged, errors = assist.merge_draft_into_cards(cards, assist.load_draft(DRAFT_PATH), mode="assist")
    assert errors == []
    assert len(merged) == 61
    for card in merged:
        assert card["human_adjudicated"] is False
        assert card["human_verdict"] is None
        assert "assist_verdict" in card and "assist_provenance" in card
    assert assist.human_adjudicated_count(merged) == 0


def test_human_mode_refuses_ai_provenance():
    cards = _cards()
    merged, errors = assist.merge_draft_into_cards(cards, assist.load_draft(DRAFT_PATH), mode="human")
    assert errors, "AI 辅助初稿必须被拒绝写成人工裁定"
    assert any("human_signed" in e or "人工" in e for e in errors)
    for card in merged:
        assert card["human_adjudicated"] is False
        assert card["human_verdict"] is None


def test_human_mode_accepts_signed_provenance_and_maps_verdict():
    draft = assist.load_draft(DRAFT_PATH)
    for row in draft:
        row["provenance"] = assist.HUMAN_PROVENANCE
        row["human_signature"] = True
    merged, errors = assist.merge_draft_into_cards(_cards(), draft, mode="human")
    assert errors == []
    assert assist.human_adjudicated_count(merged) == 61
    got = {c["card_id"]: c["human_verdict"] for c in merged}
    assert got["v2t13"] == "驳回"  # 本人 ① 打「不对」(机器印「能核实」而原句是 3 天)→ 驳回
    assert got["v2h01"] == "驳回"  # 同样 ① 打「不对」(但 ② 打「能」,自相矛盾,见上一条测试)
