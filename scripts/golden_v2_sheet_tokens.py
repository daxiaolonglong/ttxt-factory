# -*- coding: utf-8 -*-
"""把 2026-11-16 15:12 打印的《黄金核验裁定单》docx 里**实际印出来的**机器短词与等级抽出来存档。

为什么要这个:AI 判 ① 时看的是这张纸。不留下纸面实印的标签,就没法证明
「AI 当时看到的机器结论」是哪一个版本 —— 事后拿今天的机器结论去解释当时的判断,
等于伪造判据。抽出来的证据存
`10_tests/golden/adjudications/printed_sheet_tokens_20261116.jsonl`。

需要 python-docx(项目 venv 没装),用带 python-docx 的解释器跑:
    python scripts/golden_v2_sheet_tokens.py \
        --sheet 08_wiki/reports/P13-golden-adjudication-sheet.docx \
        --out   10_tests/golden/adjudications/printed_sheet_tokens_20261116.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import docx

ROOT = Path(__file__).resolve().parents[1]
TOKENS = {"能核实", "对不上", "没出处", "转述"}
ROW_RE = re.compile(r"^(\d+)\s+(v2[fht]\d\d)$")
CARD_RE = re.compile(r"(v2[fht]\d\d)")


def extract(sheet: Path) -> list[dict]:
    """定位 61 行的裁定表(组标题行不算),逐行取 行号 / 卡片编号 / 印的机器短词 / 印的等级。"""
    doc = docx.Document(str(sheet))
    rows: list[dict] = []
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if len(cells) < 7:
                continue
            m = ROW_RE.match(cells[0])
            if not m:
                continue
            row_no, card_id = int(m.group(1)), m.group(2)
            tokens = [c for c in cells if c in TOKENS]
            tiers = [c for c in cells if re.fullmatch(r"[ABCD]", c)]
            if len(tokens) != 1:
                raise SystemExit(f"{card_id}:这一行的机器短词不唯一/缺失:{tokens}")
            if len(tiers) < 1:
                raise SystemExit(f"{card_id}:这一行没有印等级字母")
            rows.append({
                "printed_row": row_no,
                "card_id": card_id,
                "printed_machine_token": tokens[0],
                "printed_tier": tiers[0],
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打勾单纸面实印标签 → 证据 JSONL")
    parser.add_argument("--sheet", default=str(ROOT / "08_wiki" / "reports" / "P13-golden-adjudication-sheet.docx"))
    parser.add_argument("--out", default=str(ROOT / "10_tests" / "golden" / "adjudications"
                                             / "printed_sheet_tokens_20261116.jsonl"))
    args = parser.parse_args(argv)

    rows = extract(Path(args.sheet))
    rows.sort(key=lambda r: r["printed_row"])
    if [r["printed_row"] for r in rows] != list(range(1, 62)):
        print(f"纸面行号不是 1..61(共 {len(rows)} 行),先核对 docx 再说", file=sys.stderr)
        return 1
    if len({r["card_id"] for r in rows}) != 61:
        print("卡片编号有重复", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 2026-11-16 15:12 打印的《黄金核验裁定单》(P13-golden-adjudication-sheet.docx)**纸面实印**的\n"
        "# 机器短词与证据等级。这是 AI 辅助初审判 ① 时的真实依据,不是今天的机器结论。\n"
    )
    out.write_text(header + "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    dist: dict[str, int] = {}
    for r in rows:
        dist[r["printed_machine_token"]] = dist.get(r["printed_machine_token"], 0) + 1
    print(f"纸面实印 {len(rows)} 行 → {out}")
    print("纸面机器短词分布:", dist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
