# -*- coding: utf-8 -*-
"""T-01(AI 口径):把 AI 辅助初审的 61 张裁定写入**独立 sidecar**,卡片文件一律不动。

背景口径(项目负责人 2026-11-17 15:06,见 07_rules/CORE_RULES.md 规则 54 第⑤条补充):
放弃逐张人工打勾,黄金集裁定改由 AI 出。但 AI 裁定**只许**写 `ai_*` 这组字段,
`human_adjudicated / human_verdict / human_fact_support / human_confirms_tier`
必须保持原样(目前全为空),AI 数量绝不冒充人工数量。

输入 = 项目负责人粘贴回传的《黄金核验裁定单》61 行(三列:①机器分类贴不贴切、
②原句能否支撑整句话、③来源等级),原样转录在本文件的 `AI_ROWS` 里,不改判、不美化。
输出 = `10_tests/golden/golden_set_v2_ai_adjudication.jsonl`(一行一张)。

取证要点(不写清楚就会造假):
- AI 看到的「机器说」是 **2026-11-16 15:12 打印的打勾单**上印的短词。纸面实印标签已由
  `scripts/golden_v2_sheet_tokens.py` 从 docx 里抽出来存进
  `10_tests/golden/adjudications/printed_sheet_tokens_20261116.jsonl`(能核实 10 / 对不上 36 / 转述 15)。
- 那份纸是 **T-03 修复之前**的机器结论。与 2026-11-17 的机器结论相比只有 6 张变了
  (v2h01/v2h02/v2h03/v2h16/v2t13/v2t14,正是 T-03 修掉的 6 张绕过卡)。
- 因此这 6 张的 AI ① 判定针对的是**纸面旧标签**。sidecar 同时记录
  `sheet_printed_machine_token / machine_at_adjudication`(判的时候)与 `machine_now`(现在),
  并按 AI 自己写明的应判标签给出 `ai_verdict_current`(派生值,理由写全,不静默改判)。
  纸面标签与今日标签一旦漂移而 sidecar 未写重述理由,生成器直接报错,不允许沿用旧判定。

用法(项目 venv,需要 import ttxt):
    .venv\\Scripts\\python.exe scripts/golden_v2_ai_adjudicate.py
    .venv\\Scripts\\python.exe scripts/golden_v2_ai_adjudicate.py --check   # 只核对不写
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_PATH = ROOT / "10_tests" / "golden" / "golden_set_v2_ai_adjudication.jsonl"
CARDS_PATH = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"

# AI 判 ① 时看的是 **2026-11-16 15:12 打印的打勾单**(P13-golden-adjudication-sheet.docx)。
# 纸面实印的机器短词已由 scripts/golden_v2_sheet_tokens.py 抽成证据文件;判据标签一律从它读,
# 不靠记忆,也不用今天的机器结论反推当时看到了什么(那样等于伪造判据)。
SHEET_TOKENS_PATH = ROOT / "10_tests" / "golden" / "adjudications" / "printed_sheet_tokens_20261116.jsonl"
TOKEN_TO_LABEL = {"能核实": "verified", "对不上": "unverified", "没出处": "no_source", "转述": "opinion"}
ZH_OF_LABEL = {v: k for k, v in TOKEN_TO_LABEL.items()}   # verified → 能核实

REASON_ATTRIBUTION = (
    "AI 理由:来源原句里没有「业内人士预计/或将/显然」这类归属,也没有「将继续发酵」的内容,"
    "机器把它归为「转述」不贴切,应判「对不上」。"
)
REASON_HEALTH_GROUP = (
    "AI 理由:来源原句没有支撑整句话;其中有拼接、错误归因、无依据预测"
    "(v2h16 把埃博拉疫苗内容与青少年运动数据错配)。"
)
REASON_V2T13 = (
    "AI 理由:来源写「开售仅3天」,工厂写「仅10天」;当时裁定单上机器标「能核实」,"
    "属于错误放行,应判「对不上」。"
)
REASON_V2T14 = (
    "AI 理由:句子日期写成 3126 年而来源是 2026 年;当时裁定单上机器标「转述」不贴切,应判「对不上」。"
)

# 15 张 AI 判「机器分类不贴切」的卡片 → 理由(逐字来自负责人回传的判定表)。
REASONS = {
    "v2f06": REASON_ATTRIBUTION,
    "v2f12": REASON_ATTRIBUTION,
    "v2f17": REASON_ATTRIBUTION,
    "v2h02": REASON_HEALTH_GROUP,
    "v2h03": REASON_HEALTH_GROUP,
    "v2h04": REASON_HEALTH_GROUP,
    "v2h09": REASON_HEALTH_GROUP,
    "v2h15": REASON_HEALTH_GROUP,
    "v2h16": REASON_HEALTH_GROUP,
    "v2t05": REASON_ATTRIBUTION,
    "v2t11": REASON_ATTRIBUTION,
    "v2t13": REASON_V2T13,
    "v2t14": REASON_V2T14,
    "v2t17": REASON_ATTRIBUTION,
    "v2t23": REASON_ATTRIBUTION,
}

# 判据①的派生:16 张标签已变更的卡片(6 张 T-03 + 10 张 T-15),按重裁写明的应判标签重述到今日口径。
CURRENT_BASIS_REREAD = {
    "v2h01": ("通过", "AI 判②=能支撑;今日机器结论已从纸面印的「转述」改为「能核实」(T-03),按今日标签 ① 成立。"
              "但按**纸面实印**的「转述」判,AI 的 ①=贴切 与 ②=能支撑 自相矛盾(规则 R4),"
              "纸面那一栏不可信,已标 needs_human_review。"),
    "v2h02": ("通过", "AI 明确说应判「对不上」;今日机器结论已是「对不上」(T-03 已修),原驳回针对的是旧标签。"),
    "v2h03": ("通过", "AI 明确说应判「对不上」;今日机器结论已是「对不上」(T-03 已修),原驳回针对的是旧标签。"),
    "v2h16": ("通过", "AI 明确说应判「对不上」(来源错配);今日机器结论已是「对不上」(T-03 已修)。"),
    "v2t13": ("通过", "AI 明确说应判「对不上」;今日机器结论已是「对不上」(数字边界修复,missing=numbers:10)。"),
    "v2t14": ("通过", "AI 明确说应判「对不上」;今日机器结论已是「对不上」(3126 年造假已被锚点拦下)。"),
    # T-15 修复(批次4,2026-11-17)之后:10 张伪造归属卡的机器结论从「转述」改为「对不上」。
    # R61 第二轮重裁按今日标签判 ① 贴切 ⇒ 通过;原 ai_verdict=驳回 针对的是旧「转述」标签。
    "v2f06": ("通过", "AI 重裁明确机器贴「对不上」正确(来源原句里没有「业内人士预计」归属);"
              "今日机器结论已是「对不上」(T-15 已修),原驳回针对的是旧「转述」标签。"),
    "v2f12": ("通过", "AI 重裁明确机器贴「对不上」正确(来源原句里没有「业内人士或将」归属);"
              "今日机器结论已是「对不上」(T-15 已修),原驳回针对的是旧「转述」标签。"),
    "v2f17": ("通过", "AI 重裁明确机器贴「对不上」正确(来源原句里没有「业内人士显然」归属);"
              "今日机器结论已是「对不上」(T-15 已修),原驳回针对的是旧「转述」标签。"),
    "v2h04": ("通过", "AI 重裁明确机器贴「对不上」正确(来源原句里没有「业内人士预计」归属);"
              "今日机器结论已是「对不上」(T-15 已修),原驳回针对的是旧「转述」标签。"),
    "v2h09": ("通过", "AI 重裁明确机器贴「对不上」正确(来源原句里没有「业内人士或将」归属);"
              "今日机器结论已是「对不上」(T-15 已修),原驳回针对的是旧「转述」标签。"),
    "v2h15": ("通过", "AI 重裁明确机器贴「对不上」正确(来源原句里没有「业内人士显然」归属);"
              "今日机器结论已是「对不上」(T-15 已修),原驳回针对的是旧「转述」标签。"),
    "v2t05": ("通过", "AI 重裁明确机器贴「对不上」正确(来源原句里没有「业内人士预计」归属);"
              "今日机器结论已是「对不上」(T-15 已修),原驳回针对的是旧「转述」标签。"),
    "v2t11": ("通过", "AI 重裁明确机器贴「对不上」正确(来源原句里没有「业内人士或将」归属);"
              "今日机器结论已是「对不上」(T-15 已修),原驳回针对的是旧「转述」标签。"),
    "v2t17": ("通过", "AI 重裁明确机器贴「对不上」正确(来源原句里没有「业内人士显然」归属);"
              "今日机器结论已是「对不上」(T-15 已修),原驳回针对的是旧「转述」标签。"),
    "v2t23": ("通过", "AI 重裁明确机器贴「对不上」正确(来源原句里没有「业内人士预计」归属);"
              "今日机器结论已是「对不上」(T-15 已修),原驳回针对的是旧「转述」标签。"),
}

# (行号, 卡片编号, ①机器分类贴切, ②原句能支撑, ③等级) —— 逐字转录负责人回传的表。
AI_ROWS = """
1 v2f01 能 C
2 v2f02 不能 C
3 v2f03 不能 C
4 v2f04 不能 C
5 v2f05 不能 C
6 v2f06 不能 C
7 v2f07 能 C
8 v2f08 不能 C
9 v2f09 不能 C
10 v2f10 不能 C
11 v2f11 不能 C
12 v2f12 不能 C
13 v2f13 能 C
14 v2f14 不能 C
15 v2f15 不能 C
16 v2f16 不能 C
17 v2f17 不能 C
18 v2f18 不能 C
19 v2f19 不能 C
20 v2h01 能 A
21 v2h02 不能 A
22 v2h03 不能 A
23 v2h04 不能 A
24 v2h05 能 A
25 v2h06 不能 A
26 v2h07 不能 A
27 v2h08 不能 A
28 v2h09 不能 A
29 v2h10 能 A
30 v2h11 不能 A
31 v2h12 不能 A
32 v2h13 不能 A
33 v2h14 不能 A
34 v2h15 不能 A
35 v2h16 不能 A
36 v2h17 不能 A
37 v2t01 能 C
38 v2t02 不能 C
39 v2t03 不能 C
40 v2t04 不能 C
41 v2t05 不能 C
42 v2t06 能 C
43 v2t07 不能 C
44 v2t08 不能 C
45 v2t09 不能 C
46 v2t10 不能 C
47 v2t11 不能 C
48 v2t12 能 C
49 v2t13 不能 C
50 v2t14 不能 C
51 v2t15 不能 C
52 v2t16 不能 C
53 v2t17 不能 C
54 v2t18 能 C
55 v2t19 不能 C
56 v2t20 不能 C
57 v2t21 不能 C
58 v2t22 不能 C
59 v2t23 不能 C
60 v2t24 不能 C
61 v2t25 不能 C
"""

# ①机器分类贴切的卡片(未出现在 REASONS 里的 46 张),按行号逐字转录自回传表的「✓/×」列。
MACHINE_APT = {
    1: True, 2: True, 3: True, 4: True, 5: True, 6: False, 7: True, 8: True, 9: True, 10: True,
    11: True, 12: False, 13: True, 14: True, 15: True, 16: True, 17: False, 18: True, 19: True,
    20: True, 21: False, 22: False, 23: False, 24: True, 25: True, 26: True, 27: True,
    28: False, 29: True, 30: True, 31: True, 32: True, 33: True, 34: False, 35: False, 36: True,
    37: True, 38: True, 39: True, 40: True, 41: False, 42: True, 43: True, 44: True, 45: True,
    46: True, 47: False, 48: True, 49: False, 50: False, 51: True, 52: True, 53: False, 54: True,
    55: True, 56: True, 57: True, 58: True, 59: False, 60: True, 61: True,
}

ADJUDICATOR = "chatgpt-assist"
ADJUDICATOR_NOTE = (
    "AI 辅助初审(项目负责人 2026-11-17 15:06 授权),原始模型版本未随裁定单回传;"
    "本记录**不是人工裁定**,不得计入人工口径。"
)

# ---------------------------------------------------------------------------
# 重判(钉死口径):两版 AI 第一轮互相打架(① 只一致 49/61),分歧全落在带归属词的句子。
# 两轮**各自独立**给出了同一把尺子(①a 句子类型贴不贴 + ①b 归属是否真实存在于来源原句;
# 来源里没有 ⇒ ① 一律记「不贴切」,应判「对不上」),口径已写进 golden_set_v2.schema.md §6。
# 下面 12 张 = 两版判定相反的那 12 张,判据必须是**今日机器标签**(不是 2026-11-16 纸面标签)。
# 历史:第一轮重判(chatgpt 侧,2026-11-17)按当时的今日标签判 10 张伪造归属卡「驳回/转述」;
# T-15 修复(批次4)后这 10 张的机器结论变为「对不上」,旧判据过期,生成器拒绝沿用(报 20 条错误)。
# R61 第二轮(2026-11-21)按**今日**标签重裁:裁定者 = 本地 127.0.0.1:8080 实际服务模型
# (qwen3.8-flash-next-iq3_s,经 ttxt.model_gate.client 调用;DeepSeek 密钥缺失未用),
# 12/12 行自洽,逐字转录自证据 10_tests/golden/adjudications/r61_recheck_local_round2.json
# (第一轮 round1 模板下 11/12 行自相矛盾的原始输出存证于 r61_recheck_local_round.json,未采用)。
# **重判只加 ai_recheck_* 字段,不加口径、不加行、不动 ai_verdict 原值(历史记录必须留着),
# 更不写 human_*。**
RECHECK_RUBRIC = "①a 句子类型贴不贴 + ①b 归属是否真实存在于来源原句;见 golden_set_v2.schema.md §6"
RECHECK_AT_BASIS = "录入时间;重判结果的原始时间戳未随表回传,不伪造"
RECHECK_ADJUDICATOR = "qwen3.8-flash-next-iq3_s"
RECHECK_ADJUDICATOR_NOTE = (
    "R61 第二轮重裁的实际执行者 = 本地 127.0.0.1:8080 Strata 模型(qwen3.8-flash-next-iq3_s),"
    "经 ttxt.model_gate.client 调用;证据见 10_tests/golden/adjudications/r61_recheck_local_round2.json。"
    "仍属 AI 口径,不是人工裁定。"
)
# card_id → (AI 所引的今日机器标签, ①重判, 理由逐字转录自第二轮证据 raw_text)
RECHECK_ROWS = {
    "v2f06": ("对不上", "通过",
              "AI 重判理由(按今日标签):句子添加了来源原句中不存在的“业内人士预计”归属及“将继续发酵”预测,"
              "机器标签“对不上”正确;引句仅陈述辞职事实,无法支撑整句。"),
    "v2f12": ("对不上", "通过",
              "AI 重判理由(按今日标签):句子添加了来源原句中不存在的“业内人士或将”归属及“将继续发酵”预测,"
              "机器标签“对不上”正确;引句仅陈述李彦宏观点,无法支撑整句。"),
    "v2f17": ("对不上", "通过",
              "AI 重判理由(按今日标签):句子添加了来源原句中不存在的“业内人士显然”归属及“将继续发酵”预测,"
              "机器标签“对不上”正确;引句仅陈述金价下跌事实,无法支撑整句。"),
    "v2h01": ("能核实", "通过",
              "AI 重判理由(按今日标签):句子与来源原句逐字一致,无额外归属或修饰,"
              "机器标签“能核实”正确;引句完全支撑句子。"),
    "v2h03": ("对不上", "通过",
              "AI 重判理由(按今日标签):句子添加了来源原句中不存在的“昆仑智能研究院表示”归属,"
              "机器标签“对不上”正确;引句主语为世卫组织专家小组,无法支撑该虚假归属。"),
    "v2h04": ("对不上", "通过",
              "AI 重判理由(按今日标签):句子添加了来源原句中不存在的“业内人士预计”归属及“将继续发酵”预测,"
              "机器标签“对不上”正确;引句仅陈述建议事实,无法支撑整句。"),
    "v2h09": ("对不上", "通过",
              "AI 重判理由(按今日标签):句子添加了来源原句中不存在的“业内人士或将”归属及“将继续发酵”预测,"
              "机器标签“对不上”正确;引句仅陈述数据事实,无法支撑整句。"),
    "v2h15": ("对不上", "通过",
              "AI 重判理由(按今日标签):句子添加了来源原句中不存在的“业内人士显然”归属及“将继续发酵”预测,"
              "机器标签“对不上”正确;引句仅陈述报告事实,无法支撑整句。"),
    "v2t05": ("对不上", "通过",
              "AI 重判理由(按今日标签):句子添加了来源原句中不存在的“业内人士预计”归属及“将继续发酵”预测,"
              "机器标签“对不上”正确;引句仅陈述行业现象,无法支撑整句。"),
    "v2t11": ("对不上", "通过",
              "AI 重判理由(按今日标签):句子添加了来源原句中不存在的“业内人士或将”归属及“将继续发酵”预测,"
              "机器标签“对不上”正确;引句仅陈述行业风气,无法支撑整句。"),
    "v2t17": ("对不上", "通过",
              "AI 重判理由(按今日标签):句子添加了来源原句中不存在的“业内人士显然”归属及“将继续发酵”预测,"
              "机器标签“对不上”正确;引句仅陈述销量事实,无法支撑整句。"),
    "v2t23": ("对不上", "通过",
              "AI 重判理由(按今日标签):句子添加了来源原句中不存在的“业内人士预计”归属及“将继续发酵”预测,"
              "机器标签“对不上”正确;引句仅陈述数据对比观点,无法支撑整句。"),
}

# 钉死口径下的**机械可核**自洽规则(判据 = 今日机器标签):
#   R5「转述」+ ①贴切 + ②不能 ⇒ 归属没有来源支撑却判标签贴切,直接违反 ①b(第二轮的老判法就是这个形态)。
def self_consistency_recheck(token_zh: str, tick1_ok: bool, support_zh: str) -> str:
    if not tick1_ok:
        return ""
    if token_zh in TOKEN_CLAIM_SUPPORTS and support_zh == "不能":
        return "R1"
    if token_zh in TOKEN_CLAIM_BLOCKED and support_zh == "能":
        return "R2"
    if token_zh in TOKEN_CLAIM_HEARSAY and support_zh == "能":
        return "R4"
    if token_zh in TOKEN_CLAIM_HEARSAY and support_zh == "不能":
        return "R5"
    return ""


# 自洽检查(用**纸面实印**的短词,不用构造真值、不用今天的机器结论):
# ①说「机器贴得贴切」却与自己的 ② 打架,说明这张的 ① 不可信,必须标出来交人工复核。
# 规则名沿用 golden_v2_assist_audit.py 的 R1/R2,并补 R4(转述也是「不当作已核实事实」的标签)。
TOKEN_CLAIM_SUPPORTS = {"能核实"}
TOKEN_CLAIM_BLOCKED = {"对不上", "没出处"}
TOKEN_CLAIM_HEARSAY = {"转述"}


def self_consistency(token_zh: str, tick1_ok: bool, support_zh: str) -> str:
    """返回 '' 表示自洽;否则返回触发的规则号。"""
    if tick1_ok:
        if token_zh in TOKEN_CLAIM_SUPPORTS and support_zh == "不能":
            return "R1"  # 纸印「能核实」、②又说撑不住,①不该打✓
        if token_zh in TOKEN_CLAIM_BLOCKED and support_zh == "能":
            return "R2"  # 纸印「对不上/没出处」、②说撑得住,①不该打✓
        if token_zh in TOKEN_CLAIM_HEARSAY and support_zh == "能":
            return "R4"  # 纸印「转述」(不当作已核实)、②说原句撑得住,①不该打✓
    return ""


def load_sheet_tokens(path: Path) -> dict[str, dict]:
    """纸面实印标签证据(card_id → 行/短词/等级)。缺文件直接退出:没有判据就不许出裁定记录。"""
    if not path.exists():
        raise SystemExit(f"缺少纸面实印标签证据:{path}\n先跑 scripts/golden_v2_sheet_tokens.py")
    out: dict[str, dict] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        rec = json.loads(raw)
        if rec["printed_machine_token"] not in TOKEN_TO_LABEL:
            raise SystemExit(f"{rec['card_id']}:纸面短词 {rec['printed_machine_token']!r} 不认识")
        out[rec["card_id"]] = rec
    return out


def build_rows(
    cards: list[dict],
    machine_now: dict[str, str],
    sheet_tokens: dict[str, dict],
) -> tuple[list[dict], list[str]]:
    """把 AI 表与卡片对齐,生成 sidecar 行;任何对不齐都算错误,不猜。"""
    errors: list[str] = []
    by_id = {c["card_id"]: c for c in cards}
    rows: list[dict] = []
    seen_rows: set[int] = set()
    for line in AI_ROWS.strip().splitlines():
        row_no, card_id, support_zh, tier = line.split()
        row_no = int(row_no)
        if row_no in seen_rows:
            errors.append(f"行号重复:{row_no}")
        seen_rows.add(row_no)
        if support_zh not in ("能", "不能") or tier not in ("A", "B", "C", "D"):
            errors.append(f"{card_id}: ②/③ 取值异常 {support_zh!r}/{tier!r}")
            continue
        card = by_id.get(card_id)
        if card is None:
            errors.append(f"{card_id}: 卡片文件里没有这张卡")
            continue
        if row_no != len(rows) + 1:
            errors.append(f"行号不连续:{row_no}(应为 {len(rows) + 1})")
        sheet = sheet_tokens.get(card_id)
        if sheet is None:
            errors.append(f"{card_id}: 打勾单里没有这张卡的实印标签")
            continue
        if sheet["printed_row"] != row_no:
            errors.append(f"{card_id}: 回传表行号 {row_no} 与打勾单纸面行号 {sheet['printed_row']} 不一致")
        if sheet["printed_tier"] != tier:
            errors.append(f"{card_id}: 回传表 ③={tier} 与纸面印的等级 {sheet['printed_tier']} 不一致")
        now = machine_now[card_id]
        at_sheet = TOKEN_TO_LABEL[sheet["printed_machine_token"]]
        stale = at_sheet != now
        verdict = "通过" if MACHINE_APT[row_no] else "驳回"
        flag = self_consistency(sheet["printed_machine_token"], MACHINE_APT[row_no], support_zh)
        if stale:
            if card_id not in CURRENT_BASIS_REREAD:
                errors.append(f"{card_id}: 纸面标签({at_sheet})与今天的机器结论({now})已不同,"
                              "必须补写「按今日标签重述」的理由,不能沿用旧判定")
                current, reason = verdict, ""
            else:
                current, reason = CURRENT_BASIS_REREAD[card_id]
        else:
            current, reason = verdict, ""
        if not stale and card_id in CURRENT_BASIS_REREAD:
            errors.append(f"{card_id}: 被登记为「判据已过期」,但纸面标签与今日机器结论一致")
        rc = RECHECK_ROWS.get(card_id)
        if rc is None:
            basis_zh = rc_verdict = rc_reason = None
            rc_flag = ""
        else:
            basis_zh, rc_verdict, rc_reason = rc
            # 重判必须按**今日**机器标签;引旧纸面标签的,直接报错(第一轮打架有一半就是这么来的)。
            if basis_zh != ZH_OF_LABEL[now]:
                errors.append(f"{card_id}: 重判所引标签「{basis_zh}」与今日机器结论"
                              f"「{ZH_OF_LABEL[now]}」不一致,重判不许按 2026-11-16 的纸面旧标签")
            # ② 沿用初审值:两版 ② 完全一致(61/61),重判表没有改判 ②。
            rc_flag = self_consistency_recheck(basis_zh, rc_verdict == "通过", support_zh)
            if rc_flag:
                errors.append(f"{card_id}: 重判 {rc_verdict} 与今日标签「{basis_zh}」+ ②{support_zh}"
                              f" 违反钉死口径({rc_flag}),见 golden_set_v2.schema.md §6")
        rows.append({
            "card_id": card_id,
            "ai_row_no": row_no,
            "category": card["category"],
            "kind": card["kind"],
            "card_source_tier": card["source_tier"],
            "ai_adjudicated": True,
            "ai_verdict": verdict,
            "ai_fact_support": "supported" if support_zh == "能" else "unsupported",
            "ai_confirms_tier": tier,
            "ai_adjudicator": ADJUDICATOR,
            "ai_adjudicator_note": ADJUDICATOR_NOTE,
            "ai_at": None,  # 由 main() 统一写录入时间
            "ai_at_basis": "录入时间;AI 初审的原始时间戳未随裁定单回传,不伪造",
            "ai_reason": REASONS.get(card_id, ""),
            "sheet_printed_machine_token": sheet["printed_machine_token"],
            "machine_at_adjudication": at_sheet,
            "machine_now": now,
            "ai_basis_stale": stale,
            "ai_verdict_current": current,
            "ai_verdict_current_reason": reason,
            "ai_self_consistency_flag": flag,
            "needs_human_review": bool(flag) or stale,
            "tier_matches_card": tier == card["source_tier"],
            # ---- 重判(钉死口径 ①a+①b;只覆盖两版打架的 12 张,原 ai_verdict 不动)----
            "ai_recheck_adjudicator": RECHECK_ADJUDICATOR if rc else None,
            "ai_recheck_adjudicator_note": RECHECK_ADJUDICATOR_NOTE if rc else None,
            "ai_recheck_at": None,  # 由 main() 统一写录入时间
            "ai_recheck_at_basis": RECHECK_AT_BASIS if rc else None,
            "ai_recheck_basis_label": basis_zh,
            "ai_recheck_verdict": rc_verdict,
            "ai_recheck_reason": rc_reason,
            "ai_recheck_rubric": RECHECK_RUBRIC if rc else None,
            "ai_recheck_flag": rc_flag or None,
            "ai_recheck_changes_recorded": bool(rc and rc_verdict != verdict),
            "ai_recheck_changes_derived": bool(rc and rc_verdict != current),
        })
    if seen_rows != set(range(1, len(cards) + 1)):
        errors.append(f"行号集合不完整:{len(seen_rows)} 行 / 卡片 {len(cards)} 张")
    return rows, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI 辅助初审结果 → 黄金集 v2 裁定 sidecar")
    parser.add_argument("--cards", default=str(CARDS_PATH))
    parser.add_argument("--sheet-tokens", default=str(SHEET_TOKENS_PATH),
                        help="打勾单纸面实印标签证据(scripts/golden_v2_sheet_tokens.py 生成)")
    parser.add_argument("--out", default=str(OUT_PATH))
    parser.add_argument("--at", default=None, help="录入时间戳(默认取当前时间)")
    parser.add_argument("--check", action="store_true", help="只核对不写文件")
    args = parser.parse_args(argv)

    spec = importlib.util.spec_from_file_location("golden_v2_harness", ROOT / "scripts" / "golden_v2_harness.py")
    harness = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(harness)

    cards, card_errors = harness.load_cards(Path(args.cards))
    if card_errors:
        print("卡片模式错误,先修卡片:", file=sys.stderr)
        for line in card_errors[:10]:
            print("  " + line, file=sys.stderr)
        return 1
    machine_now = {c["card_id"]: harness.machine_result(c)["credibility"] for c in cards}
    sheet_tokens = load_sheet_tokens(Path(args.sheet_tokens))
    rows, errors = build_rows(cards, machine_now, sheet_tokens)
    if errors:
        print("AI 裁定表与卡片对不齐:", file=sys.stderr)
        for line in errors:
            print("  " + line, file=sys.stderr)
        return 1

    at = args.at or datetime.now().astimezone().isoformat(timespec="seconds")
    for row in rows:
        row["ai_at"] = at
        if row["ai_recheck_verdict"] is not None:
            row["ai_recheck_at"] = at

    tier_ai: dict[str, int] = {}
    for row in rows:
        tier_ai[row["ai_confirms_tier"]] = tier_ai.get(row["ai_confirms_tier"], 0) + 1
    summary = {
        "ai_adjudicated": len(rows),
        "ai_verdict_raw": {
            "通过": sum(1 for r in rows if r["ai_verdict"] == "通过"),
            "驳回": sum(1 for r in rows if r["ai_verdict"] == "驳回"),
        },
        "ai_verdict_current_basis": {
            "通过": sum(1 for r in rows if r["ai_verdict_current"] == "通过"),
            "驳回": sum(1 for r in rows if r["ai_verdict_current"] == "驳回"),
        },
        "ai_fact_support": {
            "supported": sum(1 for r in rows if r["ai_fact_support"] == "supported"),
            "unsupported": sum(1 for r in rows if r["ai_fact_support"] == "unsupported"),
        },
        "ai_tier": tier_ai,
        "basis_stale_cards": [r["card_id"] for r in rows if r["ai_basis_stale"]],
        "self_consistency_flags": dict(Counter(r["ai_self_consistency_flag"] for r in rows if r["ai_self_consistency_flag"])),
        "needs_human_review": sum(1 for r in rows if r["needs_human_review"]),
        "tier_mismatch_cards": [r["card_id"] for r in rows if not r["tier_matches_card"]],
        "human_adjudicated": sum(1 for c in cards if c.get("human_adjudicated") is True),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))

    if args.check:
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# AI 辅助初审裁定 sidecar(T-01,AI 口径)。一行一张,按 card_id 与 golden_set_v2.jsonl 对齐。\n"
        "# 铁律:卡片文件里的 human_* 四个字段一律保持原样(现全为空);本文件不得出现 human_* 字段。\n"
        f"# 原始输入:项目负责人 2026-11-17 回传的《黄金核验裁定单》61 行,逐字转录于 "
        "scripts/golden_v2_ai_adjudicate.py::AI_ROWS;机器标签依据 = 2026-11-16 15:12 打印的打勾单,"
        "纸面实印短词存 10_tests/golden/adjudications/printed_sheet_tokens_20261116.jsonl。\n"
        "# 重判:12 张分歧卡按钉死口径(①a+①b)重裁;第一轮(chatgpt 侧)转录于 2026-11-17,"
        "R61 第二轮(2026-11-21)按今日标签由本地模型 qwen3.8-flash-next-iq3_s 重裁,"
        "逐字转录自 10_tests/golden/adjudications/r61_recheck_local_round2.json;"
        "重判只写 ai_recheck_* 字段,ai_verdict 与 human_* 均不动。\n"
    )
    out.write_text(header + "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"sidecar → {out}({len(rows)} 行)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
