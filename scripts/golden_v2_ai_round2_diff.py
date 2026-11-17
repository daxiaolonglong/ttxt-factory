# -*- coding: utf-8 -*-
"""T-01 复核用:两版 AI 初审(第一轮 / 第二轮)裁定对照,只出分歧清单,不入库、不改数据。

用途:第二轮裁定单(来源标注 chat.deepseek.com)与第一轮(2026-11-17 15:06 那轮)
对同一张打勾单给出了**不同**的 ① 判定。本轮**不**把第二轮结果写进 sidecar,
也**不**在度量器里加第二个口径;只把两版判定并排比对,列出必须重判的卡片,
交给项目负责人拿去让两版模型重新判定。

对照基准(取证):两版看的都是 2026-11-16 15:12 打印的那张打勾单,
纸面实印短词存 10_tests/golden/adjudications/printed_sheet_tokens_20261116.jsonl。

用法:
    .venv\\Scripts\\python.exe scripts/golden_v2_ai_round2_diff.py
    .venv\\Scripts\\python.exe scripts/golden_v2_ai_round2_diff.py --out 08_wiki\\reports\\xxx.md
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"
SIDECAR_PATH = ROOT / "10_tests" / "golden" / "golden_set_v2_ai_adjudication.jsonl"
SHEET_TOKENS_PATH = ROOT / "10_tests" / "golden" / "adjudications" / "printed_sheet_tokens_20261116.jsonl"
DEFAULT_OUT = ROOT / "08_wiki" / "reports" / "P13-ai-round2-disputes.md"

# ---------------------------------------------------------------------------
# 第二轮裁定单(项目负责人 2026-11-17 回传,来源标注 https://chat.deepseek.com/)
# 该轮自报 ai_adjudicator 写的是「ChatGPT辅助初审」,与来源标注矛盾 ⇒ 照原样存证,不替它认账。
ROUND2_SELF_REPORTED = "ChatGPT辅助初审"
ROUND2_SOURCE = "https://chat.deepseek.com/"

# 该轮 ①:61 行里只有 5 行写 ai_verdict="不对",其余 56 行写 "对"。逐字转录。
ROUND2_UNAPT = {"v2h01", "v2h02", "v2h16", "v2t13", "v2t14"}
# 该轮 ②:只有 10 行写 ai_fact_support="能",其余 51 行 "不能",没有「说不清」。逐字转录。
ROUND2_SUPPORT = {"v2f01", "v2f07", "v2f13", "v2h01", "v2h05", "v2h10",
                  "v2t01", "v2t06", "v2t12", "v2t18"}
# 该轮 ③:61 行全部 ai_confirms_tier="同意"、ai_tier_override=null ⇒ 确认卡片声明的证据级。
ROUND2_TIER = "同意"
ROUND2_REASONS = {
    "v2h01": "句子与原句逐字一致,是直接陈述,不是「某某称/据报道」,转述识别过宽",
    "v2h02": "句子是「WHO建议……导致该建议意味着……」拼接句,应判对不上,不是转述",
    "v2h16": "句子是埃博拉疫苗,原句是青少年活动,完全无关,应判对不上",
    "v2t13": "句子「开售仅10天」,原句「开售仅3天」,机器仍标能核实 —— 数字/子串锚定有漏",
    "v2t14": "日期 3126 与 2026 之差是错误,不是转述",
}
# 该轮对 v2h03 专门写下的说明(它判 ①=对,但 ②=不能):两版分歧的关键样本。
ROUND2_NOTE_V2H03 = ("该轮注明:v2h03 机器说「转述」是贴切的(句子有「昆仑智能研究院表示,」),"
                     "但原句撑不住这个归属,所以 ②=不能;度量器要把这两类分开算")

TOK2ZH = {"verified": "能核实", "unverified": "对不上", "no_source": "没出处", "opinion": "转述"}
ZH2TOK = {v: k for k, v in TOK2ZH.items()}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def read_jsonl(path: Path) -> list[dict]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#"):
            out.append(json.loads(raw))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="两版 AI 初审裁定对照(不写库)")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    harness = _load_module("golden_v2_harness_diff", ROOT / "scripts" / "golden_v2_harness.py")
    cards, card_errors = harness.load_cards(CARDS_PATH)
    if card_errors:
        print("卡片模式错误:", card_errors[:5])
        return 1
    by_id = {c["card_id"]: c for c in cards}
    sheet = {r["card_id"]: r for r in read_jsonl(SHEET_TOKENS_PATH)}
    pass_a = {r["card_id"]: r for r in read_jsonl(SIDECAR_PATH)}
    machine_now = {c["card_id"]: harness.machine_result(c)["credibility"] for c in cards}
    assert len(sheet) == len(pass_a) == len(cards) == 61

    rows = []
    for card_id in [r["card_id"] for r in read_jsonl(SIDECAR_PATH)]:
        a = pass_a[card_id]
        printed = sheet[card_id]["printed_machine_token"]
        now = machine_now[card_id]
        a_ok = a["ai_verdict"] == "通过"
        b_ok = card_id not in ROUND2_UNAPT
        rows.append({
            "card_id": card_id,
            "row_no": a["ai_row_no"],
            "kind": a["kind"],
            "tier": a["card_source_tier"],
            "printed_token": printed,
            "now_token": TOK2ZH[now],
            "stale": a["ai_basis_stale"],
            "a_ok": a_ok,
            "b_ok": b_ok,
            "a_support": "能" if a["ai_fact_support"] == "supported" else "不能",
            "b_support": "能" if card_id in ROUND2_SUPPORT else "不能",
            "a_reason": a["ai_reason"],
            "b_reason": ROUND2_REASONS.get(card_id, ""),
            "dispute_tick1": a_ok != b_ok,
            "dispute_tick2": (a["ai_fact_support"] == "supported") != (card_id in ROUND2_SUPPORT),
        })

    n = len(rows)
    agree1 = sum(1 for r in rows if not r["dispute_tick1"])
    agree2 = sum(1 for r in rows if not r["dispute_tick2"])
    disputes = [r for r in rows if r["dispute_tick1"] or r["dispute_tick2"]]

    summary = {
        "第一轮": {"①贴切": sum(1 for r in rows if r["a_ok"]), "①不贴切": sum(1 for r in rows if not r["a_ok"]),
                   "②能": sum(1 for r in rows if r["a_support"] == "能")},
        "第二轮": {"①贴切": sum(1 for r in rows if r["b_ok"]), "①不贴切": sum(1 for r in rows if not r["b_ok"]),
                   "②能": sum(1 for r in rows if r["b_support"] == "能")},
        "①一致": f"{agree1}/{n}", "②一致": f"{agree2}/{n}",
        "③一致": f"{n}/{n}(两版都确认卡片声明的证据级;第一轮写字母、第二轮写「同意」)",
        "需重判卡片": len(disputes),
        "其中判据已过期(纸面≠今日)": sorted(r["card_id"] for r in disputes if r["stale"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("\n分歧卡片:")
    for r in disputes:
        print(f"  {r['card_id']} 纸面={r['printed_token']} 今日={r['now_token']}"
              f" 第一轮①={'贴切' if r['a_ok'] else '不贴切'} 第二轮①={'贴切' if r['b_ok'] else '不贴切'}"
              + (" ②不一致" if r["dispute_tick2"] else ""))

    # ---------------- 报告 ----------------
    lines = [
        "# P13 两版 AI 初审对照与需重判清单(2026-11-17,由 scripts/golden_v2_ai_round2_diff.py 生成)",
        "",
        "**这一轮只做对照,没有把第二轮结果写进 sidecar,也没有在度量器里加第二个口径。**",
        "卡片文件的 `human_*` 四个字段仍然全空;sidecar 里目前只有第一轮那一份 AI 裁定。",
        "",
        "## 1. 两版读数对照",
        "",
        "| 列 | 第一轮(2026-11-17 15:06 那轮) | 第二轮(来源标注 chat.deepseek.com) | 两版是否一致 |",
        "|---|---:|---:|---|",
        f"| ① 机器贴切 | {summary['第一轮']['①贴切']} | {summary['第二轮']['①贴切']} | {summary['①一致']} |",
        f"| ① 机器不贴切 | {summary['第一轮']['①不贴切']} | {summary['第二轮']['①不贴切']} | 分歧 "
        f"{len([r for r in rows if r['dispute_tick1']])} 张 |",
        f"| ② 原句能支撑 | {summary['第一轮']['②能']} | {summary['第二轮']['②能']} | {summary['②一致']}(且是同一批 10 张) |",
        f"| ③ 证据级 | A 17 / C 44(字母) | 61 行全「同意」 | {summary['③一致']} |",
        "",
        f"- 两版 ① 判定一致的卡片:**{agree1}/61 = {agree1 / 61:.1%}**。"
        "两版互相不一致本身就是要报的数:AI 初审不稳定,不能拿来替代人工裁定。",
        f"- 需要重判的卡片:**{len(disputes)} 张**(见 §3)。",
        "- 第二轮自报 `ai_adjudicator` 写的是「ChatGPT辅助初审」,而负责人标注该轮答案出自 "
        "chat.deepseek.com。两处矛盾,照原样存证,没有替它改写,也没有替它认账。",
        "",
        "## 2. 分歧集中在同一个问题上",
        "",
        "两版真正相反的是**带归属词的句子**(「业内人士预计/或将/显然」「××表示」):",
        "",
        "- 第一轮判:来源原句里**没有**这个归属,句子是伪造的归属 ⇒ 机器贴「转述」不贴切,应判「对不上」;",
        "- 第二轮判:句子里**有**「××表示」这类字样 ⇒ 机器贴「转述」贴切,只是 ②=不能(原句撑不住这个归属)。",
        "",
        "也就是说两版回答的不是同一个问题:一版问「这个归属有没有来源支撑」,另一版问「句子读起来是不是转述」。",
        "**重判前必须把 ① 的定义钉死**(见 §4),否则再判三轮还是这样互相打架。",
        "",
        "另外要说明的是:第二轮点名的 5 张里,`v2t13`(数字 10 vs 3)、`v2h01`(直接陈述被贴成转述)、"
        "`v2h02`、`v2h16`、`v2t14` 在**今天的机器结论里已经改掉**了(T-03,2026-11-17 已修并推远端)。"
        "第二轮判的是 2026-11-16 打印那张纸上的旧标签。所以「机器把错当对 = 1/10 = 10%」这个读数,"
        "**只对纸面那个时点成立**;按今日标签重算,机器说「能核实」的 10 张两版都认可,是 0/10。",
        "",
        "## 3. 需重判清单(两版判定相反)",
        "",
        "| 卡片 | 纸面实印标签 | 今日机器标签 | 第一轮 ① | 第二轮 ① | 第二轮 ② | 判据是否已过期 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in disputes:
        lines.append(
            f"| `{r['card_id']}` | {r['printed_token']} | {r['now_token']} |"
            f"{'贴切' if r['a_ok'] else '不贴切'} | {'贴切' if r['b_ok'] else '不贴切'} | {r['b_support']} |"
            f"{'是(纸面≠今日)' if r['stale'] else '否'} |")
    lines += [
        "",
        f"其中 {len(summary['其中判据已过期(纸面≠今日)'])} 张"
        f"({', '.join('`' + c + '`' for c in summary['其中判据已过期(纸面≠今日)'])})"
        "两版判的都是**已经不存在**的旧标签,重判时必须按今日标签重问一遍。",
        "",
        f"> `{ROUND2_NOTE_V2H03}`",
        "",
        "## 4. 重判时要把问题钉死(可直接粘贴)",
        "",
        "```",
        "判定对象:下面每张卡片,按**当前**机器标签判,不要参考 2026-11-16 那张打印单上的旧标签。",
        "",
        "① 机器贴的处理标签贴不贴切 —— 定义钉死为两问都要答:",
        "  ①a 句子读起来的类型(直接陈述 / 转述他人 / 事实对不上)与机器贴的标签是否一致;",
        "  ①b 若机器贴的是「转述」:句子里那个归属(谁说的)在**来源原句里**是否真的存在。",
        "      来源原句里没有这个归属 ⇒ ① 记「不贴切」,应判「对不上」。不许因为句子里"
        "有「××表示」就算贴切。",
        "② 引号里的原句能不能支撑整句话(能 / 不能 / 说不清)。",
        "③ 来源等级(A 一手 / B 权威二手 / C 一般转载 / D 来源不明),要改就写「改级=X」并说明理由。",
        "输出格式:每行 `卡片号: ①贴切|不贴切(①b:是|否) ②能|不能|说不清 ③同意|改级=X 理由:...`",
        "```",
        "",
        "### 待重判卡片原文(粘贴用)",
        "",
    ]
    for r in disputes:
        card = by_id[r["card_id"]]
        lines += [
            f"**{r['card_id']}**({r['kind']},证据级 {r['tier']},机器现贴「{r['now_token']}」)",
            f"- 工厂句子:{card['claim']}",
            f"- 引句窗口原句:{card['source_excerpt']}",
            f"- 第一轮 ①:{'贴切' if r['a_ok'] else '不贴切'}"
            + (f";理由:{r['a_reason']}" if r["a_reason"] else ""),
            f"- 第二轮 ①:{'贴切' if r['b_ok'] else '不贴切'}"
            + (f";理由:{r['b_reason']}" if r["b_reason"] else ""),
            "",
        ]
    lines += [
        "## 5. 本轮明确没做的事",
        "",
        "- 没有把第二轮 61 行写进 sidecar,也没有新增第二个口径或第二套门禁;",
        "- 没有改 `expected_by_construction`、没有改路由器、没有改模型与 Strata 配置;",
        "- 没有新增治理文件:进度只记 `08_wiki/PROJECT_STATUS.md`;",
        "- 两版打架的 12 张不自行裁决,等负责人拿去重判后再落库。",
    ]
    if not args.no_write:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n报告:{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
