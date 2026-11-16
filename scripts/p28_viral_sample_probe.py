# -*- coding: utf-8 -*-
"""P28 只读取证:各类目库里"真实测得"的爆款样本有多少(决定爆款项会不会真的生效)。

只读连接(URI mode=ro),不写任何生产库。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(r"D:\ttxt\01_categories")
SQL = (
    "SELECT confidence_tier, COUNT(*) AS n, SUM(CASE WHEN score IS NOT NULL THEN 1 ELSE 0 END) AS with_score"
    " FROM viral_labels GROUP BY confidence_tier ORDER BY confidence_tier"
)
SQL_LABELS = (
    "SELECT label, COUNT(*) AS n FROM viral_labels"
    " WHERE confidence_tier='measured' AND score IS NOT NULL GROUP BY label ORDER BY n DESC"
)
SQL_KEYWORDS = "SELECT COUNT(*) FROM keywords"

out = []
for db in sorted(ROOT.glob("*/articles.db")):
    category = db.parent.name
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tiers = {row["confidence_tier"]: {"rows": row["n"], "with_score": row["with_score"]}
                 for row in con.execute(SQL)}
        measured = sum(v["with_score"] for v in tiers.values())
        labels = {row["label"]: row["n"] for row in con.execute(SQL_LABELS)}
        keywords = con.execute(SQL_KEYWORDS).fetchone()[0]
        out.append({
            "category": category,
            "measured_with_score": measured,
            "calibration_ready": measured >= 30,
            "tiers": tiers,
            "measured_labels": labels,
            "keywords": keywords,
        })
    finally:
        con.close()

print(json.dumps(out, ensure_ascii=False, indent=2))
ready = [item["category"] for item in out if item["calibration_ready"]]
print("过 30 条真实测得门槛的类目:", ready or "无")
