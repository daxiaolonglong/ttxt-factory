# -*- coding: utf-8 -*-
"""R39:裸人名/裸品牌召回 + 「事实可核率」独立放行条件。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "golden_v2_harness_r39", ROOT / "scripts" / "golden_v2_harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

from ttxt.analysis import event_identity as ei  # noqa: E402
from ttxt.writing import verifier, workflow  # noqa: E402


# ---------- 抽取层:裸人名 / 裸品牌 ----------

def test_bare_person_caught_without_predicate():
    anchors = verifier.extract_anchors("黄仁勋首次公开发声,谈及公司未来")
    assert "黄仁勋" in anchors["persons"]


def test_role_prefixed_person_caught():
    anchors = verifier.extract_anchors("英伟达 CEO 黄仁勋表示,公司将继续投入")
    assert "黄仁勋" in anchors["persons"]


def test_bare_brand_caught_from_org_context():
    text = "英伟达董事会已批准回购,英伟达CEO 黄仁勋随后回应。"
    anchors = verifier.extract_anchors(text)
    assert "英伟达" in anchors["brands"]


def test_generic_sentence_fragment_is_not_brand():
    anchors = verifier.extract_anchors("如果董事会不同意,公司董事会再议一次。")
    assert "如果" not in anchors["brands"]
    assert anchors["brands"] == []


def test_plausible_person_filters_generic_role():
    assert verifier.is_plausible_person("黄仁勋") is True
    assert verifier.is_plausible_person("专家") is False
    assert verifier.is_plausible_person("董事会") is False


# ---------- 结构化判定:召回提升不改阈值 ----------

def test_structured_accepts_when_bare_name_and_brand_overlap():
    left = "黄仁勋在发布会上表示,英伟达董事会已批准 80 亿美元回购,目标价 200 美元。"
    right = "英伟达董事会批准 80 亿美元回购,黄仁勋表示目标价为 200 美元。"
    decision = ei.decide(left, right, left_date="2026-11-18", right_date="2026-11-18")
    assert decision.same_event is True
    assert decision.shared_core == ("brand:英伟达", "person:黄仁勋")


def test_structured_rejects_same_numbers_different_entities():
    left = "蓝海科技公司宣布 30 亿元融资,CEO 李明表示估值 90 亿元。"
    right = "青禾集团发布 30 亿元项目,CEO 张伟表示估值 90 亿元。"
    decision = ei.decide(left, right, left_date="2026-11-18", right_date="2026-11-18")
    assert decision.same_event is False


# ---------- 事实可核率:写作阶段 ----------

def _evidence(credibility: str, quote: str | None, found: dict | None = None) -> dict:
    return {
        "claim": "示例事实",
        "credibility": credibility,
        "quote": quote,
        "checks": {dim: {"found": vals, "missing": []} for dim, vals in (found or {}).items()},
    }


def test_fact_checkability_counts_first_hand_and_anchor_but_not_opinion():
    evidence = [
        _evidence("verified", "原文引句", {"numbers": ["25"]}),
        _evidence("opinion", None),
        _evidence("no_source", None),
    ]
    metrics = workflow.fact_checkability(evidence)
    assert metrics["facts"] == 2          # opinion 不进分母
    assert metrics["checkable"] == 1
    assert metrics["rate"] == 0.5
    assert metrics["release_condition_met"] is False


def test_fact_checkability_met_when_all_facts_have_first_hand_source():
    metrics = workflow.fact_checkability([_evidence("unverified", "原文引句")])
    assert metrics["rate"] == 1.0
    assert metrics["release_condition_met"] is True


# ---------- 事实可核率:判断阶段(golden set) ----------

def test_harness_fact_checkability_separate_from_ab_gate():
    cards = [
        {"card_id": "a1", "source_tier": "A", "category": "technology",
         "claim": "某部门发布通知", "source_text": "某部门发布通知"},
        {"card_id": "c1", "source_tier": "C", "category": "technology",
         "claim": "王芳表示成本为 25 亿元", "source_text": "王芳表示,成本为 25 亿元。"},
        {"card_id": "c2", "source_tier": "C", "category": "technology",
         "claim": "成本大幅下降", "source_text": "成本大幅下降"},
    ]
    metrics = harness.fact_checkability_metrics(cards)
    assert metrics["cards"] == 3
    assert metrics["checkable"] == 2
    assert metrics["by_basis"]["first_hand_A"] == 1
    assert metrics["by_basis"]["anchor_in_source"] == 1
    assert metrics["by_basis"]["none"] == 1
    assert metrics["not_a_substitute_for"].startswith("A+B")
