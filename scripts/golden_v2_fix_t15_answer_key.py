# -*- coding: utf-8 -*-
"""一次性修正黄金集 v2 答案钥匙的 10 行(账本 T-15,2026-11-17)。

背景(见 10_tests/test_p13_batch4_attribution_routing.py 与 P13 批次4 报告):
`verifier.is_opinion()` 只看句子自身带不带「业内人士预计/或将/显然」这类归属词,从不检查
**来源原句里是否真的有这个归属**。10 张 `opinion_attributed` 卡的归属在绑定引句 ±1 句内
逐字找不到 ⇒ 修复前机器贴「转述」(opinion)、`missing_dims` 全空、事实门禁根本不运行。
路由器修好之后这 10 张的机器结论变成 `unverified`,答案钥匙必须跟着现实走
(改答案钥匙 = 承认构造真值本来就是「归属无出处、事实未核实」,不是把门禁调松)。

铁律(写死在代码里,不靠自觉):
· 只改 `expected_by_construction` 这一个字段,且只改下表列出的 10 行;
· 写入前断言当前值 == 表里的旧值(旧值对不上就报错退出,绝不覆盖人工/其它内容);
· 字段顺序、`ensure_ascii=False`、行尾换行、卡片数 61 全部保持原样;
· 卡片里的 claim/source_excerpt/source_url/quote*/kind/source_tier/human_* 一律不动
  (human_* 四项必须仍然为空 —— 人工裁定仍然 0/61);
· AI sidecar(10_tests/golden/golden_set_v2_ai_adjudication.jsonl)与
  纸面标签证据(adjudications/printed_sheet_tokens_20261116.jsonl)本脚本一律不碰;
· 幂等:第二次运行不再改动任何字节。

用法:
    .venv\\Scripts\\python.exe -X utf8 scripts/golden_v2_fix_t15_answer_key.py          # 执行迁移
    .venv\\Scripts\\python.exe -X utf8 scripts/golden_v2_fix_t15_answer_key.py --check  # 只校验已迁移
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"

# card_id → (旧 expected_by_construction, 新 expected_by_construction)
# 旧值取自 2026-11-17 T-15 修复前的 git HEAD;新值 = 修复后的机器结论 = 构造真值。
MIGRATIONS: list[tuple[str, str, str]] = [
    ("v2f06", "opinion", "unverified"),
    ("v2f12", "opinion", "unverified"),
    ("v2f17", "opinion", "unverified"),
    ("v2h04", "opinion", "unverified"),
    ("v2h09", "opinion", "unverified"),
    ("v2h15", "opinion", "unverified"),
    ("v2t05", "opinion", "unverified"),
    ("v2t11", "opinion", "unverified"),
    ("v2t17", "opinion", "unverified"),
    ("v2t23", "opinion", "unverified"),
]

HUMAN_FIELDS = ("human_adjudicated", "human_verdict", "human_fact_support", "human_confirms_tier")
TOTAL_CARDS = 61


def _rows(raw: str, newline: str) -> list[tuple[str, dict | None]]:
    """逐行拆成 (原始行, 解析后的 dict);注释与空行原样保留(dict 为 None)。"""
    rows: list[tuple[str, dict | None]] = []
    for line in raw.split(newline):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            rows.append((line, None))
            continue
        rows.append((line, json.loads(line)))
    return rows


def migrate(check_only: bool = False) -> int:
    raw = CARDS_PATH.read_bytes().decode("utf-8")
    # 黄金集是 CRLF 文件:换行符必须原样保留,否则整份文件都会变成字节差异。
    newline = "\r\n" if "\r\n" in raw else "\n"
    original_text = raw
    rows = _rows(original_text, newline)
    # 独立快照:后面要原地改 `rows` 里的 dict,自证零差异必须拿改之前的副本对照。
    baseline = {card["card_id"]: json.loads(line) for line, card in rows if card is not None}
    cards = [card for _line, card in rows if card is not None]
    if len(cards) != TOTAL_CARDS:
        raise SystemExit(f"[中止] 卡片数 {len(cards)} != {TOTAL_CARDS}:{CARDS_PATH}")
    by_id = {card["card_id"]: card for card in cards}
    if len(by_id) != TOTAL_CARDS:
        raise SystemExit("[中止] card_id 有重复")

    to_change: list[tuple[dict, str, str]] = []
    already: list[str] = []
    for card_id, old, new in MIGRATIONS:
        card = by_id.get(card_id)
        if card is None:
            raise SystemExit(f"[中止] 找不到卡片 {card_id}")
        if card["kind"] != "opinion_attributed":
            raise SystemExit(f"[中止] {card_id} 的 kind={card['kind']},不是 opinion_attributed")
        current = card["expected_by_construction"]
        if current == old:
            to_change.append((card, old, new))
        elif current == new:
            already.append(card_id)
        else:
            raise SystemExit(
                f"[中止] {card_id} 的 expected_by_construction={current!r},"
                f"既不是旧值 {old!r} 也不是新值 {new!r}:答案钥匙被第三方改过,先查清再迁移"
            )
    for card in cards:
        if card["card_id"] in {c for c, _o, _n in MIGRATIONS}:
            continue
        if card["expected_by_construction"] == "opinion":
            raise SystemExit(f"[中止] 表外卡片 {card['card_id']} 的 expected=opinion:本次迁移只覆盖 10 张")
    if check_only:
        if to_change:
            print(f"[未迁移] 仍有 {len(to_change)} 行待改:{', '.join(c['card_id'] for c, _o, _n in to_change)}")
            return 1
        print(f"[已迁移] {len(already)} 行的 expected_by_construction 已为新值,无需改动")
        return 0
    if not to_change:
        for card_id, _old, new in MIGRATIONS:
            if by_id[card_id]["expected_by_construction"] != new:
                raise SystemExit(f"[中止] {card_id} 期望已是 {new!r} 但实际不是")
        if any(card["expected_by_construction"] == "opinion" for card in cards):
            raise SystemExit("[中止] 仍有表外卡片 expected=opinion:答案钥匙不止改了这 10 行")
        print(f"[幂等] 10 行早已迁移完成({', '.join(already)}),本次不写任何字节")
        return 0

    # 只动这一个字段:dict 的键顺序来自 json.loads,写回时原样保留
    for card, old, new in to_change:
        card["expected_by_construction"] = new

    out_lines: list[str] = []
    for line, card in rows:
        out_lines.append(line if card is None else json.dumps(card, ensure_ascii=False))
    CARDS_PATH.write_bytes(newline.join(out_lines).encode("utf-8"))

    # ------- 自证零差异:除这 10 行的 expected_by_construction 外,其它一切逐字段一致 -------
    written = CARDS_PATH.read_bytes().decode("utf-8")
    after = {
        c["card_id"]: c
        for _l, c in _rows(written, "\r\n" if "\r\n" in written else "\n")
        if c is not None
    }
    changed_ids = {card["card_id"] for card, _o, _n in to_change}
    untouched_fields = 0
    for card_id, card_before in baseline.items():
        card_after = after[card_id]
        expected_delta = {key for key in card_before if card_before[key] != card_after.get(key)}
        allowed = {"expected_by_construction"} if card_id in changed_ids else set()
        if expected_delta != allowed:
            raise SystemExit(f"[中止] {card_id} 除答案钥匙外还动了这些字段:{sorted(expected_delta)}")
        if list(card_before) != list(card_after):
            raise SystemExit(f"[中止] {card_id} 的字段顺序变了")
        untouched_fields += len(card_before) - (1 if card_id in changed_ids else 0)
    for card in after.values():
        if card["human_adjudicated"] is not False:
            raise SystemExit(f"[中止] {card['card_id']} 的 human_adjudicated 不是 False")
        if any(card[field] is not None for field in HUMAN_FIELDS[1:]):
            raise SystemExit(f"[中止] {card['card_id']} 的 human_* 不再是空值:人工裁定必须仍为 0/61")
    if len(after) != TOTAL_CARDS:
        raise SystemExit("[中止] 写回后卡片数变了")

    print(f"[迁移] 改动 {len(to_change)} 行的 expected_by_construction({CARDS_PATH}):")
    for card, old, new in to_change:
        print(f"  {card['card_id']}: {old} -> {new}")
    print(f"[自证] 未改字段 {untouched_fields} 个(51 张全字段 + 10 张各 20 字段)全部逐字一致;"
          f"字段顺序不变;卡片 {TOTAL_CARDS} 张;human_* 仍全空。")
    return 0


if __name__ == "__main__":
    raise SystemExit(migrate(check_only="--check" in sys.argv[1:]))
