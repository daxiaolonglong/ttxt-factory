"""R48 T-10:从现有语料分布计算去模板化类目预算(确定性,可复算)。

规则(先分布、后阈值,不拍脑袋):
- 数据源:``scripts/template_probe.py`` 生成的快照 JSON(默认 03_intelligence/template_probe-20261119.json,38 版本);
- 每类目:marker/百句、parallel/百句 各自取该类目历史版本的 **p75**(线性插值);
- 类目历史版本数 < min_basis(默认 8)→ 该类目两项都改用**全体合并分布的 p75**,
  并在 basis 里如实标注 pooled_fallback 与样本数;
- 读数四舍五入到 2 位后写入 ``00_control/template_budgets.json`` 冻结;
  后续生成链路只读这份冻结预算,不回落实时改写。

用法:
    python scripts/template_budget_compute.py [--probe 03_intelligence/template_probe-20261119.json] [--out 00_control/template_budgets.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ttxt.writing.style import _percentile

MIN_BASIS = 8
DEFAULT_PROBE = "03_intelligence/template_probe-20261119.json"
DEFAULT_OUT = "00_control/template_budgets.json"


def compute(probe: dict, min_basis: int = MIN_BASIS) -> dict:
    per_cat: dict[str, dict[str, list[float]]] = {}
    pooled_m: list[float] = []
    pooled_p: list[float] = []
    for cat, c in (probe.get("categories") or {}).items():
        ms, ps = [], []
        for r in c.get("rows") or []:
            if r.get("sentences"):
                ms.append(100.0 * r["marker_hits"] / r["sentences"])
                ps.append(100.0 * r["parallel_pattern_hits"] / r["sentences"])
        per_cat[cat] = {"marker": ms, "parallel": ps}
        pooled_m.extend(ms)
        pooled_p.extend(ps)

    budgets: dict[str, dict] = {}
    for cat, dist in per_cat.items():
        n = len(dist["marker"])
        if n >= min_basis:
            m = _percentile(dist["marker"], 0.75)
            p = _percentile(dist["parallel"], 0.75)
            basis = "category_p75"
        else:
            m = _percentile(pooled_m, 0.75)
            p = _percentile(pooled_p, 0.75)
            basis = "pooled_p75_fallback"
        budgets[cat] = {
            "marker_per_100_sent": round(m, 2),
            "parallel_per_100_sent": round(p, 2),
            "basis": basis,
            "basis_drafts": n,
        }
    return {
        "rule": (
            "类目预算=该类目历史版本每百句命中数 p75(线性插值);历史版本数<"
            f"{min_basis} 的类目改用全体合并分布 p75 并标注 pooled_p75_fallback;"
            "读数四舍五入 2 位冻结,生成链路只读该文件"
        ),
        "probe_source": None,
        "probe_generated_at": probe.get("generated_at"),
        "min_basis": min_basis,
        "min_sentences": 12,
        "pooled_basis_drafts": len(pooled_m),
        "pooled_p75": {
            "marker_per_100_sent": round(_percentile(pooled_m, 0.75), 2),
            "parallel_per_100_sent": round(_percentile(pooled_p, 0.75), 2),
        },
        "budgets": budgets,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", default=DEFAULT_PROBE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    probe = json.loads(Path(args.probe).read_text(encoding="utf-8"))
    doc = compute(probe)
    doc["probe_source"] = str(args.probe)
    out = Path(args.out)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(doc, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
