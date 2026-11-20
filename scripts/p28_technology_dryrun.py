# -*- coding: utf-8 -*-
"""P28 生产数据干跑:把 technology 库**备份副本**摆到 07_tmp 下,跑新评分,看真实前后变化。

绝不连生产库(只读连接也会在生产目录留边车,P24 已证):
先用 ``Connection.backup()`` 复制(WAL 内容一起带上),再在副本上算分。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

# R51(P2-2):仓库根从 __file__ 推导(本脚本在 <root>/scripts/ 下),可用 TTXT_ROOT 覆盖,
# 不再写死 D:\ttxt——换机器/换盘符也能定位。
ROOT = Path(os.environ.get("TTXT_ROOT") or Path(__file__).resolve().parent.parent)

sys.path.insert(0, str(ROOT))

from ttxt.analysis import keywords  # noqa: E402

PROD = ROOT / "01_categories" / "technology" / "articles.db"
COPY = ROOT / "07_tmp" / "p28_viral_signal" / "technology_copy.db"


def snapshot(con, label: str) -> dict[str, dict]:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT k.keyword, k.status, p.collected, p.quality_articles, p.avg_text_length, p.score"
        " FROM keywords k JOIN keyword_performance p ON p.keyword_id = k.id ORDER BY p.score DESC, k.keyword"
    ).fetchall()
    out = {row["keyword"]: dict(row) for row in rows}
    print(f"--- {label}")
    for row in rows:
        print(f"  {row['keyword']:<12} collected={row['collected']:>3} quality={row['quality_articles']:>3}"
              f" avg_len={row['avg_text_length']:>7} score={row['score']} status={row['status']}")
    return out


def main() -> int:
    COPY.parent.mkdir(parents=True, exist_ok=True)
    if COPY.exists():
        COPY.unlink()
    src = sqlite3.connect(PROD)
    dst = sqlite3.connect(COPY)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    before_con = sqlite3.connect(COPY)
    try:
        before = snapshot(before_con, "改前(生产副本,旧公式)")
    finally:
        before_con.close()

    stats = keywords.score_keywords("technology", db_path=COPY)

    after_con = sqlite3.connect(COPY)
    try:
        after = snapshot(after_con, "改后(同一副本,真实爆款表现参与评分)")
    finally:
        after_con.close()

    viral = stats["viral"]
    print("--- 爆款项状态")
    print(json.dumps({k: v for k, v in viral.items() if k != "details"}, ensure_ascii=False, indent=2))
    print("--- 每个关键词的爆款信号")
    for keyword, detail in viral["details"].items():
        print(f"  {keyword:<12} matched={detail['matched_articles']:>3}"
              f" measured_hits={detail['measured_hits']:>3} signal={detail['signal']} {detail['reason']}")
    print("--- 分数变化")
    for keyword in sorted(before, key=lambda k: -(after.get(k, {}).get("score") or 0)):
        old = before[keyword]["score"]
        new = after.get(keyword, {}).get("score")
        delta = None if old is None or new is None else round(new - old, 4)
        print(f"  {keyword:<12} {old} -> {new}  Δ={delta}")
    print(f"类目: {stats['keywords']} 个关键词 / gap={stats['zero_coverage']}"
          f" / 反哺候选={stats['feedback_candidates']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
