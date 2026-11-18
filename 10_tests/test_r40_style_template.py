"""R40:模板化度量与评分×表现对照的只读口径钉子。"""

from __future__ import annotations

import json
from pathlib import Path

from ttxt.writing.style import FORMULAIC_MARKERS, template_signals


def test_template_signals_counts_formulaic_markers():
    text = "首先，看数据。与此同时，还要看结构。综上所述，结论成立。"
    sig = template_signals(text)
    assert sig["marker_hits"] == 3
    assert sig["markers"] == {"首先": 1, "与此同时": 1, "综上所述": 1}
    assert sig["sentences"] == 3


def test_template_signals_detects_repeated_opening_and_parallel():
    text = "从芯片到算力，产业链在重排。从模型到应用，竞争在加速。"
    sig = template_signals(text)
    assert sig["parallel_pattern_hits"] == 2
    assert sig["duplicate_sentence_share"] == 0.0


def test_template_signals_clean_text_has_no_markers():
    sig = template_signals("英伟达发布了新架构。黄仁勋在发布会上回答了提问。")
    assert sig["marker_hits"] == 0
    assert sig["repeated_opening_kinds"] == 0
    assert sig["sentence_length_cv"] is not None


def test_template_signals_empty_is_zero_not_error():
    sig = template_signals("")
    assert sig["sentences"] == 0
    assert sig["marker_hits"] == 0
    assert sig["sentence_length_cv"] is None


def test_formulaic_marker_list_is_frozen_contract():
    # 该清单是度量口径的一部分,改动需显式同步报告与账本
    assert "首先" in FORMULAIC_MARKERS and "与此同时" in FORMULAIC_MARKERS
    assert len(set(FORMULAIC_MARKERS)) == len(FORMULAIC_MARKERS)


def test_score_vs_performance_report_is_honest_about_small_sample():
    """若本地产物存在,必须如实标注 insufficient_sample 且不合并两个表现池。"""
    path = Path("03_intelligence/score_vs_performance-20261119.json")
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data["categories"].values():
        main = item["main"]
        if main["n"] < data["min_samples"]:
            assert main["status"] == "insufficient_sample"
        pools = item["performance_pools"]
        # measured 与 measured_reads 必须分别成池,不得合并成一个分数
        assert "combined" not in pools
