# -*- coding: utf-8 -*-
"""P29:辅助裁定**第二版**(15 行第①勾按正确定义重裁)的录入与护栏(先红后绿)。

背景(如实):第一版辅助稿把 ① 理解成"机器判的实对不对",于是 15 行打了「不对」——
其中 10 行机器给的标签其实是「转述」(观点/归属路由,不宣称已被来源证实)。
本轮交回的第二版把 ① 改成问"**机器使用的处理标签贴不贴切**",15 行全部改「对」,
并给出 5 行的逐条理由。**交回的仍是 AI 裁定,AI 自己声明不能签真人名字。**

要守住的底线:
1. 第二版只进 `assist_*`,`human_*` 一个字节都不写,门禁(人工裁定 0/61)照旧不过;
2. 15 行改的就是上一版打「不对」的那 15 行,不能顺手多改别行;
3. 逐行理由必须与卡里存的原文对得上(不许只走形式);
4. `adjudicator_type == "ai"` 的草案,即使把 `human_signature` 伪造成 True 也绝不许写 `human_*`。

T-15(P13 批次4,2026-11-17)之后如实登记的后果:那 10 行机器当时贴的是「转述」,
修好路由器后构造真值变成「对不上」⇒ 纸面 ①=「对」与钥匙不再自洽(61 → 51),
修订器也拒绝再批量重生成这份草案。两者都是**变严**,不是回归。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.golden_v2_assist_audit as assist
import scripts.golden_v2_revise_assist_draft as revise

ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"
V1_PATH = ROOT / "10_tests" / "golden" / "adjudications" / "assist_draft_chatgpt_20261116.jsonl"
V2_PATH = ROOT / "10_tests" / "golden" / "adjudications" / "assist_draft_chatgpt_20261116_v2.jsonl"

# T-15 的 10 张伪造归属卡(P13 批次4,2026-11-17 已修路由器)。
# 纸面(2026-11-16)印的机器词是「转述」,而答案钥匙跟着修好的机器结论变成了「对不上」
# ⇒ 这 10 行的 ①=「对」失去了依据,审计按 R3 记矛盾,重生成也必须被拒绝。
T15_CARDS = {"v2f06", "v2f12", "v2f17", "v2h04", "v2h09", "v2h15",
             "v2t05", "v2t11", "v2t17", "v2t23"}


def _cards() -> list[dict]:
    return [json.loads(l) for l in CARDS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def _by_id() -> dict[str, dict]:
    return {c["card_id"]: c for c in _cards()}


def test_v2_sidecar_has_61_rows_and_still_no_human_marks():
    draft = assist.load_draft(V2_PATH)
    assert len(draft) == 61
    assert len({r["card_id"] for r in draft}) == 61
    assert all(r["draft_version"] == 2 for r in draft)
    # AI 自陈是 AI:裁定者类型必须写明,不许留空蒙混
    assert {r["adjudicator_type"] for r in draft} == {"ai"}
    assert all(r["human_signature"] is False for r in draft)
    assert all(r["tick1_semantics"] == assist.TICK1_SEMANTICS for r in draft)
    # 卡片集本身一个字节都没被改过:人工裁定仍是 0/61
    assert assist.human_adjudicated_count(_cards()) == 0


def test_only_the_15_disputed_rows_changed_and_they_are_the_right_15():
    v1 = assist.load_draft(V1_PATH)
    v2 = assist.load_draft(V2_PATH)
    diff = assist.diff_drafts(v1, v2)
    disputed = {r["card_id"] for r in v1 if r["tick1_machine"] == "不对"}
    assert len(disputed) == 15
    assert diff["added_ids"] == [] and diff["removed_ids"] == []

    # 三个勾 + 行号 + 卡片编号:只有 15 行的 ① 被动过,其余一律不许动
    judged_fields = {"tick1_machine", "tick2_fact_support", "tick3_tier",
                     "assist_tier_letter", "printed_row", "assist_row"}
    judged_flips = [f for f in diff["flips"] if f["field"] in judged_fields]
    assert {f["card_id"] for f in judged_flips} == disputed
    assert all(f["field"] == "tick1_machine" and f["before"] == "不对" and f["after"] == "对"
               for f in judged_flips)
    assert len(judged_flips) == 15

    # 其它被改动的只能是从前留白的审计元数据(版本号/理由/裁定者类型/口径/是否浏览过来源)
    metadata = {"draft_version", "reason", "adjudicator_type", "tick1_semantics",
                "browsed_by_ai", "source_pages_checked", "recorded_at", "changed_from_v1"}
    others = {f["field"] for f in diff["flips"]} - judged_fields
    assert others <= metadata, f"越界改动了这些字段:{sorted(others - metadata)}"
    assert diff["changed_card_ids"]
    metadata_only = set(diff["changed_card_ids"]) - disputed
    by2 = {r["card_id"]: r for r in v2}
    assert all(by2[c]["changed_from_v1"] is True for c in disputed)
    assert all(by2[c]["changed_from_v1"] is False for c in metadata_only)


def test_v2_totals_are_pinned_and_label_definition_is_self_consistent():
    cards, v2 = _cards(), assist.load_draft(V2_PATH)
    report = assist.audit(cards, v2)
    assert report["tick1"] == {"对": 61}          # 第二版汇总:61 对 / 0 不对
    assert report["tick2"] == {"不能": 51, "能": 10}
    assert report["tick3"] == {"同意": 61}
    # ① 现在定义成"标签贴不贴切" ⇒ 与构造真值应当自洽(这条本身不是准确率,只是口径自洽)。
    # T-15(批次4,2026-11-17)之后自洽数从 61 掉到 51:那 10 张伪造归属卡的构造真值
    # 随修好的机器结论变成 unverified(该贴「对不上」),而 2026-11-16 纸面印的机器词仍是
    # 「转述」⇒ 审计按 R3 记 10 条矛盾。这是修复的**代价**:这 10 行得重裁一轮,
    # 绝不许把纸面或答案钥匙改回去凑数。
    assert report["machine_token_matches_construction"] == 51
    assert report["tick1_matches_token"] == 51
    assert len(report["tick1_contradictions"]) == 10
    assert {c["card_id"] for c in report["tick1_contradictions"]} == T15_CARDS
    assert all(c["rule"] == "R3" and "对不上" in c["why"] for c in report["tick1_contradictions"])


def test_row_numbers_and_card_ids_still_agree_with_printed_sheet():
    v2 = assist.load_draft(V2_PATH)
    assert sorted(r["printed_row"] for r in v2) == list(range(1, 62))
    # 本轮交回的锚点:打印第 13 行 = 酷派 10 天/3 天陷阱卡
    by_printed = {r["printed_row"]: r for r in v2}
    for printed, card_id in revise.DECLARED_ANCHORS:
        assert by_printed[printed]["card_id"] == card_id, f"第 {printed} 行不是 {card_id}"
    # 等级字母必须与卡片 source_tier 一致(61/61)
    assert assist.audit(_cards(), v2)["tier_mismatch"] == []


def test_five_explanations_match_the_text_stored_in_the_cards():
    """逐条核对本人交回的解释,拿卡里存的原文当证据,不是走形式。"""
    by_id, v2 = _by_id(), {r["card_id"]: r for r in assist.load_draft(V2_PATH)}
    for card_id, reason in revise.EXPLANATIONS.items():
        assert reason in v2[card_id]["reason"], f"{card_id} 的理由没落到草案里"

    # v2h01:工厂句与原句逐字一致,机器标「能核实」贴切
    c = by_id["v2h01"]
    assert c["claim"] == c["source_excerpt"] and c["kind"] == "positive"
    # v2t13:数字被改 —— 工厂句写 10 天,原文写 3 天
    c = by_id["v2t13"]
    assert "10天" in c["claim"] and "3天" in c["source_excerpt"] and "10天" not in c["source_excerpt"]
    # v2h03:机构张冠李戴 —— 工厂句主体是「昆仑智能研究院」,原文里没有这个名字
    c = by_id["v2h03"]
    assert "昆仑智能研究院" in c["claim"] and "昆仑智能研究院" not in c["source_text"]
    assert "世界卫生组织" in c["source_text"]
    # v2h02:强行添加因果 —— 原文里「导致」只出现在「导致当前疫情的病原体」,
    #        工厂句把两句话用「导致」焊在一起
    c = by_id["v2h02"]
    assert "试验导致该建议意味着" in c["claim"]
    assert "。该建议意味着" in c["source_text"] and "试验导致该建议" not in c["source_text"]
    # v2h16:来源完全不相关 —— 工厂句讲的埃博拉疫苗,不在它绑定的那篇原文里
    c = by_id["v2h16"]
    assert "埃博拉" not in c["source_text"] and "身体活动" in c["source_text"]


def test_every_v2_row_carries_a_non_empty_reason():
    for row in assist.load_draft(V2_PATH):
        assert isinstance(row["reason"], str) and len(row["reason"].strip()) >= 8


def test_reviser_now_refuses_to_regenerate_v2_after_t15(tmp_path):
    """T-15 修复前:重跑修订器逐字复现已提交的 v2 草案(转录不可漂)。
    T-15 修复后:构造真值变了(10 张伪造归属卡 ⇒ 「对不上」),纸面上印的机器词还是「转述」,
    修订器必须**拒绝**批量沿用旧草案的 ①=「对」,要求这 10 行单独重裁,而不是悄悄照抄。
    这条变红 = 要么有人把钥匙/纸面改回去凑数,要么这道护栏被削弱。
    """
    out = tmp_path / "v2_regen.jsonl"
    with pytest.raises(assist.DraftError) as raised:
        revise.write_draft(V1_PATH, out)
    message = str(raised.value)
    assert "机器贴的词 '转述'" in message and "'unverified'" in message and "对不上" in message, message
    assert any(cid in message for cid in T15_CARDS), message
    assert not out.exists(), "护栏拦下了却仍然写出草案 = 沿用旧判定"


def test_ai_draft_cannot_be_written_as_human_adjudication():
    cards, draft = _cards(), assist.load_draft(V2_PATH)
    merged, errors = assist.merge_draft_into_cards(cards, draft, mode="human")
    assert errors and assist.human_adjudicated_count(merged) == 0


def test_forged_human_signature_still_rejected_because_adjudicator_is_ai(tmp_path):
    """把 provenance 与 human_signature 都伪造成"人工",但裁定者是 AI ⇒ 仍然拒绝。"""
    forged = tmp_path / "forged.jsonl"
    rows = assist.load_draft(V2_PATH)
    for r in rows:
        r["provenance"] = "human_signed"
        r["human_signature"] = True
        r["adjudicator_type"] = "ai"       # AI 自己声明:我是 AI,不能签真人名字
    forged.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                      encoding="utf-8")
    draft = assist.load_draft(forged)
    merged, errors = assist.merge_draft_into_cards(_cards(), draft, mode="human")
    assert errors, "伪造签名 + AI 裁定者竟然通过了"
    assert assist.human_adjudicated_count(merged) == 0
    assert any("AI" in e or "裁定者" in e for e in errors)


def test_load_draft_rejects_bad_types_in_new_fields(tmp_path):
    row = dict(assist.load_draft(V2_PATH)[0])
    row["draft_version"] = "二"
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(assist.DraftError):
        assist.load_draft(bad)

    row = dict(assist.load_draft(V2_PATH)[0])
    row.pop("reason")                     # 第二版必须逐行带理由
    bad2 = tmp_path / "bad2.jsonl"
    bad2.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(assist.DraftError):
        assist.load_draft(bad2)

    row = dict(assist.load_draft(V2_PATH)[0])
    row["adjudicator_type"] = "机器人"
    bad3 = tmp_path / "bad3.jsonl"
    bad3.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(assist.DraftError):
        assist.load_draft(bad3)


def test_cli_audit_reports_the_v2_numbers(capsys):
    code = assist.main(["--draft", str(V2_PATH)])
    captured = capsys.readouterr()
    assert code == 0
    assert "61" in captured.out
    assert "①" in captured.out
