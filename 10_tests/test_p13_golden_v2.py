"""P13 批次1:黄金核验集 v2 的模式校验与质量门禁测试。

关键约定(与 10_tests/golden/golden_set_v2.schema.md 一致):
- 机器结论与「构造预期」的一致率只是规则吻合度,不是质量分数;
- 只有 human_adjudicated=true 的卡片进入质量口径;
- 已人工裁定卡片 < MIN_HUMAN_CASES 时,quality_claim 必须为 NOT_AVAILABLE。
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "golden_v2_harness", ROOT / "scripts" / "golden_v2_harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

SRC_TEXT = (
    "2026年3月,星海科技公司发布新一代开源模型。"
    "王芳表示,该模型训练成本为 25 亿元,推理速度提升 3.5%。"
    "由于采用了稀疏注意力架构,长文本处理成本大幅下降。"
)


def _card(**overrides) -> dict:
    card = {
        "card_id": "v2x01",
        "category": "technology",
        "fact_type": "number",
        "kind": "positive",
        "claim": "王芳表示,该模型训练成本为 25 亿元",
        "source_url": "https://example.com/src/42",
        "source_title": "星海科技发布新模型",
        "source_publisher": "星海科技 Newsroom",
        "source_tier": "A",
        "corroborating_urls": [],
        "independence_note": "一手新闻稿,人工待确认",
        "source_text": SRC_TEXT,
        "source_article_id": None,
        "source_excerpt": "王芳表示,该模型训练成本为 25 亿元",
        "quote_offset": SRC_TEXT.index("王芳表示"),
        "expected_by_construction": "verified",
        "human_adjudicated": False,
        "human_verdict": None,
        "human_fact_support": None,
        "human_confirms_tier": None,
        "note": "fixture",
    }
    card.update(overrides)
    return card


def _write(tmp_path: Path, cards: list[dict]) -> Path:
    path = tmp_path / "cards.jsonl"
    path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cards) + "\n", encoding="utf-8")
    return path


def test_valid_card_has_no_schema_errors(tmp_path):
    cards, errors = harness.load_cards(_write(tmp_path, [_card()]))
    assert errors == []
    assert len(cards) == 1


def test_missing_field_is_rejected(tmp_path):
    card = _card()
    del card["source_tier"]
    _cards, errors = harness.load_cards(_write(tmp_path, [card]))
    assert any("缺必填字段 source_tier" in e for e in errors)


def test_tier_b_requires_independent_source(tmp_path):
    card = _card(source_tier="B", corroborating_urls=[])
    _cards, errors = harness.load_cards(_write(tmp_path, [card]))
    assert any("tier=B" in e for e in errors)


def test_excerpt_must_be_verbatim_substring_of_source_text(tmp_path):
    card = _card(source_excerpt="王芳表示训练成本为 25 亿元")  # 少了逗号 → 非逐字
    _cards, errors = harness.load_cards(_write(tmp_path, [card]))
    assert any("逐字片段" in e for e in errors)


def test_empty_source_text_blocks_offline_replay(tmp_path):
    card = _card(source_text="", source_excerpt="")
    _cards, errors = harness.load_cards(_write(tmp_path, [card]))
    assert any("source_text 为空" in e for e in errors)


def test_machine_result_binds_quote_and_flags_perturbed_number():
    bound = harness.machine_result(_card())
    assert bound["credibility"] == "verified"
    assert bound["quote_verbatim_ok"] is True

    perturbed = _card(claim="王芳表示,该模型训练成本为 32 亿元", kind="negative_perturbed",
                      expected_by_construction="unverified")
    result = harness.machine_result(perturbed)
    assert result["credibility"] == "unverified"
    assert any(dim.startswith("numbers:") for dim in result["missing_dims"])


def test_hedge_word_routes_whole_claim_to_opinion_known_boundary():
    # 已记录的设计边界:句中出现 hedge 词即整句路由为观点,数字锚点不再走事实门禁
    result = harness.machine_result(_card(claim="王芳预计,该模型训练成本为 999 亿元",
                                          kind="opinion_attributed",
                                          expected_by_construction="opinion"))
    assert result["credibility"] == "opinion"
    assert result["opinion_markers"]


def test_quality_claim_not_available_without_human_adjudication():
    cards = [_card(card_id=f"v2x{i:02d}") for i in range(harness.MIN_HUMAN_CASES)]
    metrics = harness.evaluate(cards)
    assert metrics["quality_claim"] == "NOT_AVAILABLE"
    assert any(f"{harness.MIN_HUMAN_CASES}" in item for item in metrics["gate_failures"])
    assert metrics["machine_vs_human"]["accuracy"] is None
    assert metrics["machine_vs_human"]["false_accept_rate"] is None


def _gate_satisfying_cards() -> list[dict]:
    kinds = sorted(harness.KINDS)
    cats = sorted(harness.CATEGORIES)
    cards = []
    n = 0
    for category in cats:
        for index in range(17):
            kind = kinds[index % len(kinds)]
            negative = kind in harness.NEGATIVE_KINDS
            n += 1
            cards.append(_card(
                card_id=f"v2g{n:03d}", category=category, kind=kind,
                source_tier="A", corroborating_urls=[],
                expected_by_construction="unverified" if negative else "verified",
                human_adjudicated=True, human_verdict="通过",
                human_fact_support="supported" if not negative else "unsupported",
                human_confirms_tier="A",
            ))
    return cards


def test_quality_claim_available_only_when_every_gate_is_met():
    cards = _gate_satisfying_cards()
    metrics = harness.evaluate(cards)
    assert len(cards) >= harness.MIN_HUMAN_CASES
    assert metrics["quality_claim"] == "AVAILABLE", metrics["gate_failures"]
    assert metrics["machine_vs_human"]["accuracy"] is not None
    assert metrics["machine_vs_human"]["false_accept_rate"] is not None
    assert metrics["source_independence"]["independent_share_of_adjudicated"] == 1.0


def test_dropping_one_category_reopens_gate():
    cards = [c for c in _gate_satisfying_cards() if c["category"] != "health"]
    metrics = harness.evaluate(cards)
    assert metrics["quality_claim"] == "NOT_AVAILABLE"
    assert any("health" in item for item in metrics["gate_failures"])


def test_machine_and_human_disagreement_is_counted():
    cards = _gate_satisfying_cards()
    # 负样本:机器判 verified,人工「驳回」= 机器没有拦住 → 不计入误放行
    flipped = [c for c in cards if c["kind"] in harness.NEGATIVE_KINDS][:3]
    for card in flipped:
        card["human_verdict"] = "驳回"
    metrics = harness.evaluate(cards)
    assert metrics["machine_vs_human"]["reject_驳回"] == 3
    assert metrics["machine_vs_human"]["accuracy"] < 1.0


def test_shipped_v2_file_is_schema_clean_and_gated():
    path = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"
    if not path.exists():
        pytest.skip("golden_set_v2.jsonl 尚未生成(先跑 scripts/build_golden_set_v2.py)")
    cards, errors = harness.load_cards(path)
    assert errors == []
    assert cards
    metrics = harness.evaluate(cards)
    # 候选集刚建立时必然没有人工裁定,门禁必须拒绝给出质量结论
    if metrics["adjudicated_count"] < harness.MIN_HUMAN_CASES:
        assert metrics["quality_claim"] == "NOT_AVAILABLE"


def test_gate_is_machine_detectable_via_exit_code(tmp_path):
    """NOT_AVAILABLE 必须以退出码 2 暴露,否则 CI / 每周巡检无法判定。"""
    script = str(ROOT / "scripts" / "golden_v2_harness.py")

    failing = _write(tmp_path, [_card(card_id=f"v2e{i:03d}") for i in range(30)])
    bad = subprocess.run(
        [sys.executable, "-X", "utf8", script, "--cards", str(failing), "--no-report"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert bad.returncode == 2, bad.stderr[-500:]

    passing = _write(tmp_path, _gate_satisfying_cards())
    good = subprocess.run(
        [sys.executable, "-X", "utf8", script, "--cards", str(passing), "--no-report"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert good.returncode == 0, good.stderr[-500:]
