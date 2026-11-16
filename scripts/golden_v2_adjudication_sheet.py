# -*- coding: utf-8 -*-
"""生成《黄金核验裁定单》——给本人手填打勾的一张表(61 行 × 3 个勾)。

需要 **python-docx**(项目 venv 没装,用带 python-docx 的解释器跑):
    python scripts/golden_v2_adjudication_sheet.py --flat 07_tmp/golden_sheet/cards_flat.json

前置:先跑 `scripts/golden_v2_dump_flat.py`(用项目 venv)生成扁平 JSON。

设计要点(不要改):
- 只印「工厂写的句子 + 来源原句 + 机器结论」,不印 `kind`、不印 `expected_by_construction`:
  印了就等于把答案写在脸上,人工裁定就被污染,`false_accept_rate` 随之失真。
- 三个勾对应卡片字段:①→`human_verdict`(对=通过 / 不对=驳回),②→`human_fact_support`
  (能=supported / 不能=unsupported / 说不清=needs_more_evidence),③→`human_confirms_tier`。
- 表格必须固定布局(见 fix_table):不设 tblLayout/tblGrid,列宽被内容撑乱,同一份内容会从 12 页涨到 24 页。
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
CAT_NAME = {"finance": "财经", "health": "健康", "technology": "科技"}
CAT_ORDER = ("finance", "health", "technology")
MACHINE_ZH = {
    "verified": "能核实",
    "unverified": "对不上",
    "no_source": "没出处",
    "opinion": "转述",
}
TIER_ZH = {"A": "A 官方/一手原文件", "B": "B 两家以上互不相关的大媒体都这么说",
           "C": "C 只有一家,或自媒体转载", "D": "D 没出处"}
COLS = [1.0, 16.6, 1.5, 0.9, 1.9, 3.0, 2.4]  # 合计 27.3cm(可打印宽 27.5cm);来源机构挪到附录,句子栏才够宽
AP_COLS = [1.0, 2.6, 5.0, 4.6, 0.9, 13.2]


def set_font(run, size=9.0, bold=False, color=None, italic=False, name="Microsoft YaHei"):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.name = name
    if color:
        run.font.color.rgb = RGBColor(*color)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def para(doc, text, size=9.0, bold=False, space_after=3, color=None, italic=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(0)
    set_font(p.add_run(text), size=size, bold=bold, color=color, italic=italic)
    return p


def cell_text(cell, lines, size=8.5):
    first = True
    for text, style in lines:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        p.paragraph_format.space_after = Pt(1)
        p.paragraph_format.space_before = Pt(0)
        set_font(p.add_run(text), size=style.get("size", size), bold=style.get("bold", False),
                 color=style.get("color"), italic=style.get("italic", False))


def fix_table(table, widths_cm):
    """固定表格布局:tblLayout=fixed + tblGrid 明确列宽 + 每个 tc 的 tcW(合并格按跨列宽度合计)。"""
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    for tag in ("w:tblLayout", "w:tblCellMar"):
        for node in tbl_pr.findall(qn(tag)):
            tbl_pr.remove(node)
    tbl_pr.append(tbl_pr.makeelement(qn("w:tblLayout"), {qn("w:type"): "fixed"}))
    margins = tbl_pr.makeelement(qn("w:tblCellMar"), {})
    for side, w in (("left", 60), ("right", 60), ("top", 20), ("bottom", 20)):  # 1/20 pt
        margins.append(margins.makeelement(qn(f"w:{side}"), {qn("w:w"): str(w), qn("w:type"): "dxa"}))
    tbl_pr.append(margins)
    grid = tbl.tblGrid
    for col in list(grid):
        grid.remove(col)
    for w in widths_cm:
        grid.append(grid.makeelement(qn("w:gridCol"), {qn("w:w"): str(int(w * 567))}))
    table.autofit = False
    for row in table.rows:
        idx = 0
        for tc in row._tr.tc_lst:
            tc_pr = tc.get_or_add_tcPr()
            for old in tc_pr.findall(qn("w:tcW")):
                tc_pr.remove(old)
            span_node = tc_pr.find(qn("w:gridSpan"))
            span = int(span_node.get(qn("w:val"))) if span_node is not None else 1
            span = max(1, min(span, len(widths_cm) - idx))
            width = sum(widths_cm[idx:idx + span])
            # tcW 在 tcPr 里必须排在 gridSpan 之前,所以 insert(0, ...)
            tc_pr.insert(0, tc_pr.makeelement(qn("w:tcW"), {qn("w:w"): str(int(width * 567)), qn("w:type"): "dxa"}))
            idx += span


def add_field(par, instr):
    """段落里插一个 Word 域(如 PAGE / NUMPAGES),页脚页码用。"""
    for part in (("begin", None), (None, instr), ("end", None)):
        run = par._p.makeelement(qn("w:r"), {})
        if part[0]:
            char = run.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): part[0]})
            run.append(char)
        else:
            text = run.makeelement(qn("w:instrText"), {})
            text.set(qn("xml:space"), "preserve")
            text.text = part[1]
            run.append(text)
        par._p.append(run)


def footer(section, prefix):
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(p.add_run(prefix), size=8, color=(120, 120, 120))
    add_field(p, "PAGE")
    set_font(p.add_run(" / "), size=8, color=(120, 120, 120))
    add_field(p, "NUMPAGES")
    for r in p.runs:
        set_font(r, size=8, color=(120, 120, 120))


def build(rows: list[dict], out: Path, generated_at: str) -> None:
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = Cm(29.7), Cm(21.0)
    section.left_margin = section.right_margin = Cm(1.1)
    section.top_margin, section.bottom_margin = Cm(1.1), Cm(1.0)

    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(9)
    footer(section, "黄金核验裁定单 · 行号 1-61 · 第 ")

    # ------------------------------------------------------------------ 说明页
    para(doc, "黄金核验裁定单(61 行 · 你只要打勾)", size=17, bold=True, space_after=4)
    para(doc, f"头条智能工厂 · {generated_at} 生成 · 打印后手写打勾,勾完交回给我录入,不用你动电脑",
         size=9.5, color=(96, 96, 96), space_after=8)
    para(doc, "【你要做的事】61 行,每行打 3 个勾,大约 25–35 分钟。看不懂就勾「说不清」——不算错,比瞎猜有用。",
         size=10.5, bold=True, space_after=6)
    para(doc, "每行只看两句:大字是「工厂写出来的句子」,下面小字是「来源里的原句」。三个勾都围绕这两句打。",
         size=10, space_after=8)

    box = doc.add_table(rows=1, cols=3)
    box.style = "Table Grid"
    box.alignment = WD_TABLE_ALIGNMENT.LEFT
    heads = [
        ("第①勾:机器说得对不对?", "表里「机器说」那栏已经写明它的结论。\n□对     □不对"),
        ("第②勾:来源原句撑得住这句话吗?", "只看小字那句原句,够不够证明上面那句。\n□能     □不能     □说不清"),
        ("第③勾:证据等级你同意吗?", "「级」那栏是机器标的字母。同意就勾同意;\n不同意就在后面写你认为的字母。\n□同意     改成:____"),
    ]
    for i, (title, body) in enumerate(heads):
        cell = box.rows[0].cells[i]
        cell_text(cell, [(title, {"size": 10, "bold": True}), (body, {"size": 9.5})])
    fix_table(box, [8.8, 8.8, 8.8])

    para(doc, "", size=4, space_after=2)
    para(doc, "【字母是啥意思】A 官方/一手的原文件 · B 两家以上互不相关的大媒体都这么说 · C 只有一家或自媒体转载 · D 没出处",
         size=9.5, space_after=3)
    para(doc, "【「机器说」那栏只有四个短词】能核实 = 它说这句话能在来源里逐字对上;对不上 = 它说来源在,可句子跟来源对不上;"
              "没出处 = 它说这句话找不到出处;转述 = 它说这句只是「某某媒体报道称」。", size=9.5, space_after=3)
    para(doc, "【来源是哪来的】每行的来源机构、网址、以及这些来源是不是互相抄的,都写在最后的附录里(按行号对应),不用翻页找。",
         size=9.5, space_after=3)
    para(doc, "【表里为什么不标哪张是陷阱题】故意不标。标了就不用判断了,这张单子就白做。", size=9.5, space_after=3)
    para(doc, "【勾完之后我做什么】我按行号把每个勾录回卡片文件(字段 human_verdict / human_fact_support / human_confirms_tier),"
              "再跑度量器算「机器跟人判得一致的比例」和「机器把错的当对的比例」(这个最关键),写进报告。"
              "在那之前,工厂不许宣称「事实核验达标」。", size=9.5, space_after=3)
    para(doc, "【一句实话先说在前头】61 行全勾完,门禁还差一条:A/B 级证据要占一半以上,现在只有 17/61(28%)。"
              "那条是我的活(去补真出处),不关你的事。这一轮你只管打勾。",
         size=9.5, bold=True, color=(150, 30, 30), space_after=8)

    tally = doc.add_table(rows=1, cols=4)
    tally.style = "Table Grid"
    for i, text in enumerate(["我一共勾了 ____ 行(应 61)", "机器说对 ____ 行", "机器说不对 ____ 行", "签字/日期:__________"]):
        cell_text(tally.rows[0].cells[i], [(text, {"size": 10.5})])
    fix_table(tally, [6.6, 6.6, 6.6, 6.6])

    doc.add_page_break()

    # ------------------------------------------------------------------ 打勾主表
    para(doc, "打勾表(61 行 · 按类目分组;「级」= 机器标的证据级)", size=12, bold=True, space_after=6)
    table = doc.add_table(rows=1, cols=len(COLS))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    heads = ["行", "工厂写出来的句子(要判断的话)   /   小字 = 来源里的原句", "机器\n说\n(见首页)", "级",
             "① 机器说得对吗\n□对  □不对", "② 原句撑得住吗\n□能 □不能 □说不清", "③ 证据级\n□同意 改:___"]
    for i, title in enumerate(heads):
        cell_text(table.rows[0].cells[i], [(title, {"size": 8.5, "bold": True})])

    row_no = 0
    for cat in CAT_ORDER:
        group = [r for r in rows if r["category"] == cat]
        if not group:
            continue
        sep = table.add_row()
        merged = sep.cells[0]
        for other in sep.cells[1:]:
            merged = merged.merge(other)
        cell_text(merged, [(f"{CAT_NAME[cat]}组 · {len(group)} 行(卡片编号 {group[0]['card_id']} … {group[-1]['card_id']})",
                            {"size": 9.5, "bold": True, "color": (40, 70, 120)})])
        for card in group:
            row_no += 1
            row = table.add_row()
            values = [
                (str(row_no), {"size": 9, "bold": True}),
                (card["claim"], {"size": 8.5}),
                (MACHINE_ZH.get(card["machine"], str(card["machine"])), {"size": 8.5, "bold": True}),
                (card["source_tier"], {"size": 9, "bold": True}),
                ("□对   □不对", {"size": 8.5}),
                ("□能  □不能  □说不清", {"size": 8.5}),
                ("□同意  改:____", {"size": 8.5}),
            ]
            for i, (text, style) in enumerate(values):
                cell_text(row.cells[i], [(text, style)])
            cell_text(row.cells[1], [(f"原句:{card['source_excerpt']}", {"size": 7.5, "italic": True, "color": (90, 90, 90)})])
    fix_table(table, COLS)

    doc.add_page_break()

    # ------------------------------------------------------------------ 附录
    para(doc, "附录 · 第③勾参考:每条来源的「独立性说明」(机器记的一句话)", size=12, bold=True, space_after=6)
    appendix = doc.add_table(rows=1, cols=len(AP_COLS))
    appendix.style = "Table Grid"
    for i, title in enumerate(["行", "卡片编号", "来源机构", "网址域名", "级", "独立性说明(打第③勾时参考)"]):
        cell_text(appendix.rows[0].cells[i], [(title, {"size": 9, "bold": True})])
    row_no = 0
    for cat in CAT_ORDER:
        for card in [r for r in rows if r["category"] == cat]:
            row_no += 1
            row = appendix.add_row()
            publisher = card["source_publisher"]
            if card["corroborating"]:
                publisher += f"(另 {card['corroborating']} 条并列)"
            values = [(str(row_no), {"size": 8.5}), (card["card_id"], {"size": 8.5}),
                      (publisher, {"size": 8.5}), (card["source_host"], {"size": 8}),
                      (card["source_tier"], {"size": 8.5, "bold": True}),
                      (card["independence_note"] or "(卡片里没写)", {"size": 8.5})]
            for i, (text, style) in enumerate(values):
                cell_text(row.cells[i], [(text, style)])
    fix_table(appendix, AP_COLS)

    para(doc, "", size=6, space_after=2)
    para(doc, "等级定义再抄一遍,免得来回翻:" + " · ".join(TIER_ZH[k] for k in "ABCD"), size=9, space_after=2)
    para(doc, "本单子由 scripts/golden_v2_adjudication_sheet.py 生成;数据来自 10_tests/golden/golden_set_v2.jsonl,"
              "机器结论与 scripts/golden_v2_harness.py 同源。生成时间 " + generated_at, size=8, color=(120, 120, 120))

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print(f"已生成:{out}({out.stat().st_size} 字节)")

    # 自查:重新打开,确认 61 行都在、句子没缺
    back = Document(str(out))
    main_table = [t for t in back.tables if len(t.columns) == len(COLS)][0]
    text = "\n".join(p.text for r in main_table.rows for c in r.cells for p in c.paragraphs)
    missing = [r["card_id"] for r in rows if r["claim"][:18] not in text]
    print(f"复查:主表行数={len(main_table.rows)}(1 表头 + 3 分组 + {len(rows)} 行),"
          f"缺句={len(missing)} 卡片总数={len(rows)}")
    if missing:
        print("缺失卡片:", missing[:10])
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="黄金核验集 v2 → 人工裁定单打勾表(docx)")
    parser.add_argument("--flat", default=str(ROOT / "07_tmp" / "golden_sheet" / "cards_flat.json"))
    parser.add_argument("--out", default=str(ROOT / "08_wiki" / "reports" / "P13-golden-adjudication-sheet.docx"))
    args = parser.parse_args(argv)
    rows = [dict(r) for r in __import__("json").loads(Path(args.flat).read_text(encoding="utf-8"))]
    out = Path(args.out)
    if out.suffix.lower() != ".docx":
        out = out / "P13-golden-adjudication-sheet.docx"
    build(rows, out, datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
