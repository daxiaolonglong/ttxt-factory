# -*- coding: utf-8 -*-
"""把黄金核验集 v2 的卡片压平成裁定单用的扁平 JSON(含**机器结论**)。

用**项目 venv** 跑(需要 import ttxt):
    .venv\\Scripts\\python.exe scripts/golden_v2_dump_flat.py --out 07_tmp/golden_sheet/cards_flat.json

机器结论走 `scripts/golden_v2_harness.py::machine_result`,与度量器同源,
避免裁定单上印的「机器说」和 harness 算出来的不是一回事。只读卡片,不改任何数据。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_harness():
    spec = importlib.util.spec_from_file_location("golden_v2_harness", ROOT / "scripts" / "golden_v2_harness.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="黄金核验集 v2 → 裁定单用扁平 JSON")
    parser.add_argument("--cards", default=str(ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"))
    parser.add_argument("--out", default=str(ROOT / "07_tmp" / "golden_sheet" / "cards_flat.json"))
    args = parser.parse_args(argv)

    harness = load_harness()
    cards, errors = harness.load_cards(Path(args.cards))
    if errors:
        print(f"卡片模式错误 {len(errors)} 条,先修卡片再出裁定单:", file=sys.stderr)
        for line in errors[:10]:
            print("  " + line, file=sys.stderr)
        return 1

    rows = []
    for card in cards:
        machine = harness.machine_result(card)
        url = card.get("source_url") or ""
        rows.append(
            {
                "card_id": card["card_id"],
                "category": card["category"],
                "fact_type": card["fact_type"],
                "kind": card["kind"],  # 裁定单上**不印**,只留作事后对账,防止人工被答案污染
                "claim": card["claim"],
                "source_publisher": card["source_publisher"],
                "source_title": card.get("source_title") or "",
                "source_tier": card["source_tier"],
                "corroborating": len(card.get("corroborating_urls") or []),
                "independence_note": card.get("independence_note") or "",
                "source_excerpt": card.get("source_excerpt") or "",
                "source_host": url.split("/")[2] if "://" in url else "",
                "machine": machine.get("credibility"),
                "expected": card.get("expected_by_construction"),
            }
        )

    rows.sort(key=lambda r: r["card_id"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    dist = {}
    for row in rows:
        dist[row["machine"]] = dist.get(row["machine"], 0) + 1
    print(f"卡片 {len(rows)} 张 → {out}")
    print("机器结论分布:", dist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
