# -*- coding: utf-8 -*-
"""P41:黄金集窗口重采样(方案 A)与只读指标 pair_independence_share 的回归测试。

钉住四件事:
1. 窗口集分母仍是 61(类目 25/19/17),每种 kind ≥3,B 卡佐证来源必须跨站;
2. 新旧重合算术自洽(overlap + new = 61,eliminated = 61);
3. `pair_independence_share` 是纯函数,经 `--pair-report` 注入后**不改变**门禁结论;
4. 旧黄金集 `golden_set_v2.jsonl` 一字节未动(哈希钉死),原读数 A17/B0/C44 仍可复算。
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from ttxt.analysis import source_identity as si

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "golden_v2_harness", ROOT / "scripts" / "golden_v2_harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

WINDOW_CARDS = ROOT / "10_tests" / "golden" / "golden_set_v2_window.jsonl"
WINDOW_META = ROOT / "10_tests" / "golden" / "golden_set_v2_window_build.json"
OLD_CARDS = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"
PAIR_REPORT = ROOT / "03_intelligence" / "source_identity" / "window-pairs-20261119.json"

#: 旧集冻结指纹:任何重写都会让下面的读数对比失去意义
OLD_SHA256 = "b1138f9c5dd638ae4bee922f671f165788da3e1f87283cfe6e1ae78d13f938c2"


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def test_window_set_keeps_61_cards_and_category_quota():
    cards, errors = harness.load_cards(WINDOW_CARDS)
    assert errors == []
    assert len(cards) == 61
    counts = {}
    for card in cards:
        counts[card["category"]] = counts.get(card["category"], 0) + 1
    assert counts == {"technology": 25, "finance": 19, "health": 17}


def test_window_set_kind_floor():
    cards, _ = harness.load_cards(WINDOW_CARDS)
    kinds = {}
    for card in cards:
        kinds[card["kind"]] = kinds.get(card["kind"], 0) + 1
    assert set(kinds) == harness.KINDS
    assert all(n >= harness.MIN_PER_KIND for n in kinds.values()), kinds


def test_b_tier_cards_corroborate_across_hosts():
    cards, _ = harness.load_cards(WINDOW_CARDS)
    b_cards = [c for c in cards if c["source_tier"] == "B"]
    assert len(b_cards) >= 1, "窗口内应至少存在 1 组同事件独立机构对"
    for card in b_cards:
        assert card["corroborating_urls"], card["card_id"]
        for url in card["corroborating_urls"]:
            assert _host(url) != _host(card["source_url"]), (card["card_id"], url)


def test_window_metadata_matches_cards():
    meta = json.loads(WINDOW_META.read_text(encoding="utf-8"))
    cards, _ = harness.load_cards(WINDOW_CARDS)
    assert meta["window_days"] == 7
    assert meta["window_start"] < meta["window_end"]
    assert meta["window_basis"].strip()
    assert meta["category_counts"] == {"technology": 25, "finance": 19, "health": 17}
    assert sum(meta["tier_counts"].values()) == len(cards) == 61
    tiers = {}
    for card in cards:
        tiers[card["source_tier"]] = tiers.get(card["source_tier"], 0) + 1
    assert tiers == meta["tier_counts"]
    assert meta["pair_stats"]["pair_independence_share"]["meaning"].startswith("同题候选")


def test_overlap_arithmetic():
    meta = json.loads(WINDOW_META.read_text(encoding="utf-8"))
    overlap = meta["overlap_with_old"]
    card_level = overlap["card_level"]
    assert card_level["overlap"] + card_level["new"] == 61
    assert card_level["eliminated"] == 61
    new_cards = sum(v["new_cards"] for v in overlap["by_category"].values())
    old_cards = sum(v["old_cards"] for v in overlap["by_category"].values())
    assert new_cards == 61 and old_cards == 61
    for key, value in overlap["by_category"].items():
        assert value["old_cards"] == value["new_cards"], key
        assert value["overlap_cards"] + value["new_only_cards"] == value["new_cards"], key


def test_pair_independence_share_pure_function():
    pairs = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    rejected = [{"a": 1, "b": 5}, {"a": 2, "b": 6}]
    metrics = si.pair_independence_share(pairs, rejected)
    assert metrics["independent_pairs"] == 2
    assert metrics["candidate_pairs"] == 4
    assert metrics["value"] == 0.5
    empty = si.pair_independence_share([], [])
    assert empty["value"] is None


def test_pair_report_is_read_only_for_gate():
    cards, _ = harness.load_cards(WINDOW_CARDS)
    baseline = harness.evaluate(cards)
    pair_metrics, errors = harness.load_pair_metrics(PAIR_REPORT)
    assert errors == []
    with_pairs = harness.evaluate(cards, pair_metrics=pair_metrics)
    assert with_pairs["gate_failures"] == baseline["gate_failures"]
    assert with_pairs["quality_claim"] == baseline["quality_claim"] == "NOT_AVAILABLE"
    assert with_pairs["source_independence"]["pair_independence_share"]["value"] is not None
    assert baseline["source_independence"]["pair_independence_share"]["value"] is None


def test_old_golden_set_is_untouched_and_reproducible():
    raw = OLD_CARDS.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == OLD_SHA256
    cards, errors = harness.load_cards(OLD_CARDS)
    assert errors == []
    assert len(cards) == 61
    tiers = {}
    for card in cards:
        tiers[card["source_tier"]] = tiers.get(card["source_tier"], 0) + 1
    assert tiers.get("A") == 17 and tiers.get("B", 0) == 0 and tiers.get("C") == 44
    metrics = harness.evaluate(cards)
    assert metrics["machine_vs_construction"]["agreement"] == 61
