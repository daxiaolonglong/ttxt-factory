# -*- coding: utf-8 -*-
"""R38 对照集:用**已知假对**验收结构化「事件同一性」通路,0 误配才允许进窗口集。

项目负责人的验收条件(原话):
- 新通路必须先用已知假对(同题不同事 / 转载冒充独立 / 同域名互配)跑对照集,
  **0 误配**才允许进入窗口集;**误配即回退**;
- 转载 / 同源 / 官方通稿判定优先级更高,新通路不得绕过;
- 对照集与全量配对必须走**同一个判定函数**(``event_identity.judge_pair``),
  避免"验证一套、上线另一套"。

四组对照(全部取窗口内真实文章,不造样本):
1. ``same_domain``     同一域名同日互配 —— 必须停在 domain 阶段;
2. ``same_agency``     跨域名但规范机构 id 相同(官方通稿归一口径) —— 必须停在 independence;
3. ``reprint_copy``    跨机构、跨域名,但正文 5-gram 重合度 ≥ 转载阈值 —— 必须按转载拒绝;
4. ``known_false``     人工标注的同题不同事/转载冒充独立样本 —— 必须全部拒绝。

另有 ``known_true`` 两组人工核过的真同事件(行云科技、房企退市),必须被接受;
窗口内其余被接受的对会逐对标 ``unlabeled_accept``,要求人工复核后才可入卡。

用法:
    python scripts/event_identity_validate.py [--as-of 2026-11-19] [--days 7]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import build_golden_set_v2_window as win          # noqa: E402
from ttxt.analysis import event_identity as ei    # noqa: E402
from ttxt.analysis import source_identity as si  # noqa: E402

OUT = ROOT / "03_intelligence" / "source_identity" / "r38-control-set-{as_of}.json"
SAMPLE_LIMIT = 12          # 同类对照只留前若干对做可读样本,断言仍跑全量

#: 人工标注的假对(标题片段定位,url 直接钉死;窗口内找不到记 skipped,不伪造)
KNOWN_FALSE = [
    {
        "label": "同题不同事:新易盛(东财) × 天孚通信(每经)——两家公司被标题/行业词拉近",
        "category": "finance",
        "left_url": "http://finance.eastmoney.com/news/1345,202611183934093662.html",
        "right_url": "https://www.nbd.com.cn/articles/2026-11-18/4651797.html",
        "expect_stage": "structured",
    },
    {
        "label": "同题不同事:科创50行情(东财) × 国常会救市谣言(中新网)——同日财经稿共享行情数字",
        "category": "finance",
        "left_url": "http://finance.eastmoney.com/news/1345,202611183933933885.html",
        "right_url": "https://www.chinanews.com.cn/cj/2026/11-18/10734213.shtml",
        "expect_stage": "structured",
    },
    {
        "label": "转载冒充独立:商务部答问(东财) × 中新网同稿——不同域名、不同署名,正文 99.7% 重合",
        "category": "finance",
        "left_url": "http://finance.eastmoney.com/news/1351,202611183934332462.html",
        "right_url": "https://www.chinanews.com.cn/cj/2026/11-18/10734424.shtml",
        "expect_stage": "structured",
    },
]
#: 人工核过的真同事件(逐字读正文确认是同一件事),必须被接受
KNOWN_TRUE = [
    {
        "label": "真同事件:行云科技 20 亿元 AI 词元跨境订单(东财 × 每经)",
        "category": "finance",
        "left_url": "http://finance.eastmoney.com/news/1354,202611183934169142.html",
        "right_url": "https://www.nbd.com.cn/articles/2026-11-18/4651857.html",
    },
    {
        "label": "真同事件:多家房企股价跌破 1 元拉响退市警报(第一财经 × 每经)",
        "category": "finance",
        "left_url": "",
        "right_url": "",
        "left_title": "股价下挫拉响多家房企退市警报",
        "right_title": "两天三家上市房企股价跌破1元",
    },
]


def _items(rows: list[dict], identities: dict[str, si.Identity]) -> list[dict]:
    return [{"url": r["url"], "title": r["title"], "domain": si.domain_of(r["url"]),
             "source": r["source"], "date": win._d(r["published_at"]), "text": r.get("text") or ""}
            for r in rows if r["url"] in identities]


def _load(category: str, start: str, end: str, probes: dict[str, si.Identity]) -> tuple[list[dict], dict]:
    rows = []
    for art in win.load_articles(category):
        if not (start <= win._d(art["published_at"]) <= end):
            continue
        ident, _basis = win.identity_of(art, probes)
        if ident.kind == si.KIND_UNKNOWN or ident.agency is None:
            continue
        rows.append({**art, "identity": ident})
    identities = {r["url"]: r["identity"] for r in rows}
    return _items(rows, identities), identities


def _verdict(left: dict, right: dict, identities: dict, signals: dict) -> dict:
    return ei.judge_pair(left, right, identities, signals=signals)


def _row(left: dict, right: dict, v: dict) -> dict:
    return {
        "left": {"url": left["url"], "title": left["title"], "domain": left["domain"],
                 "date": left["date"], "agency": v["agencies"][0]},
        "right": {"url": right["url"], "title": right["title"], "domain": right["domain"],
                  "date": right["date"], "agency": v["agencies"][1]},
        "title_score_aux": v["title_score_aux"], "stage": v["stage"],
        "accepted": v["accepted"], "reason": v["reason"], "evidence": v["evidence"],
    }


def run(as_of: date, days: int) -> dict:
    start = (as_of - timedelta(days=days - 1)).isoformat()
    end = as_of.isoformat()
    probes = win.load_probe_identities()
    groups = {k: {"evaluated": 0, "accepted": 0, "samples": []}
              for k in ("same_domain", "same_agency", "reprint_copy")}
    accepted_pairs: list[dict] = []
    signals: dict[str, ei.Signals] = {}
    items_by_cat: dict[str, list[dict]] = {}
    identities: dict[str, si.Identity] = {}

    for category in win.TARGETS:
        items, ids = _load(category, start, end, probes)
        items_by_cat[category] = items
        identities.update(ids)
        by_day: dict[str, list[dict]] = {}
        for item in items:
            by_day.setdefault(item["date"], []).append(item)
        for day, same_day in by_day.items():
            for i, left in enumerate(same_day):
                for right in same_day[i + 1:]:
                    v = _verdict(left, right, ids, signals)
                    if left["domain"] == right["domain"]:
                        key = "same_domain"
                    elif not si.independent(ids[left["url"]], ids[right["url"]]):
                        key = "same_agency"
                    else:
                        copy = (v["evidence"] or {}).get("copy_containment") or 0.0
                        if copy < ei.COPY_CONTAINMENT:
                            if v["accepted"]:
                                accepted_pairs.append({"category": category, **_row(left, right, v)})
                            continue
                        key = "reprint_copy"
                    group = groups[key]
                    group["evaluated"] += 1
                    if v["accepted"]:
                        group["accepted"] += 1
                    if len(group["samples"]) < SAMPLE_LIMIT or v["accepted"]:
                        group["samples"].append({"category": category, **_row(left, right, v)})

    def _resolve(entry: dict) -> tuple[dict | None, dict | None, dict]:
        cat = entry["category"]
        items = items_by_cat.get(cat) or []
        by_url = {it["url"]: it for it in items}
        left = by_url.get(entry.get("left_url") or "")
        right = by_url.get(entry.get("right_url") or "")
        if left is None and entry.get("left_title"):
            left = next((it for it in items if entry["left_title"] in (it["title"] or "")), None)
        if right is None and entry.get("right_title"):
            right = next((it for it in items if entry["right_title"] in (it["title"] or "")), None)
        return left, right, by_url

    def _judge(entry: dict) -> dict:
        left, right, _ = _resolve(entry)
        if left is None or right is None:
            missing = [side for side, it in (("left", left), ("right", right)) if it is None]
            return {"label": entry["label"], "status": "skipped",
                    "reason": f"窗口内找不到样本({','.join(missing)}),不伪造判定"}
        v = _verdict(left, right, identities, signals)
        return {"label": entry["label"], "status": "evaluated",
                "expected": entry.get("expect_stage") or "accepted",
                **_row(left, right, v)}

    known_false = [_judge(e) for e in KNOWN_FALSE]
    known_true = [_judge(e) for e in KNOWN_TRUE]

    accepted_keys = {frozenset((p["left"]["url"], p["right"]["url"])) for p in accepted_pairs}
    for entry in known_true:
        if entry["status"] != "evaluated":
            continue
        key = frozenset((entry["left"]["url"], entry["right"]["url"]))
        if entry["accepted"]:
            accepted_keys.discard(key)

    problems: list[str] = []
    for key in ("same_domain", "same_agency", "reprint_copy"):
        g = groups[key]
        if g["accepted"]:
            problems.append(f"{key}: {g['accepted']} 对被接受(必须为 0)")
    for entry in known_false:
        if entry["status"] == "evaluated" and entry["accepted"]:
            problems.append(f"known_false 被接受:{entry['label']}")
    for entry in known_true:
        if entry["status"] != "evaluated":
            problems.append(f"known_true 样本缺失:{entry['label']}")
        elif not entry["accepted"]:
            problems.append(f"known_true 被拒:{entry['label']}")
    unlabeled = [p for p in accepted_pairs
                 if frozenset((p["left"]["url"], p["right"]["url"])) in accepted_keys]

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(), "window_days": days,
        "window_start": start, "window_end": end,
        "rule_stages": ["domain", "identity", "independence", "reprint_copy", "structured"],
        "params": {
            "min_shared_entities": ei.MIN_SHARED_ENTITIES,
            "min_shared_core_entities": ei.MIN_SHARED_CORE_ENTITIES,
            "min_shared_strong_entities": ei.MIN_SHARED_STRONG_ENTITIES,
            "min_shared_numbers": ei.MIN_SHARED_NUMBERS,
            "min_shared_number_ratio": ei.MIN_SHARED_NUMBER_RATIO,
            "copy_containment": ei.COPY_CONTAINMENT,
            "min_tokens_for_copy": ei.MIN_TOKENS_FOR_COPY,
        },
        "groups": groups,
        "known_false": known_false,
        "known_true": known_true,
        "accepted_pairs": accepted_pairs,
        "unlabeled_accepts": unlabeled,
        "problems": problems,
        "verdict": "PASS" if not problems else "FAIL",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R38 结构化事件同一性对照集验证")
    parser.add_argument("--as-of", default="2026-11-19")
    parser.add_argument("--days", type=int, default=win.WINDOW_DAYS_DEFAULT)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    as_of = date.fromisoformat(args.as_of)
    receipt = run(as_of, args.days)
    out = Path(args.out) if args.out else Path(str(OUT).format(as_of=as_of.strftime("%Y%m%d")))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "verdict": receipt["verdict"],
        "groups": {k: {"evaluated": v["evaluated"], "accepted": v["accepted"]}
                   for k, v in receipt["groups"].items()},
        "known_false": [{k: e.get(k) for k in ("label", "status", "accepted", "reason")}
                        for e in receipt["known_false"]],
        "known_true": [{k: e.get(k) for k in ("label", "status", "accepted")}
                       for e in receipt["known_true"]],
        "accepted_pairs": [{"left": p["left"]["title"], "right": p["right"]["title"],
                            "core": (p["evidence"] or {}).get("shared_core"),
                            "numbers": (p["evidence"] or {}).get("shared_numbers"),
                            "copy": (p["evidence"] or {}).get("copy_containment")}
                           for p in receipt["accepted_pairs"]],
        "unlabeled_accepts": len(receipt["unlabeled_accepts"]),
        "problems": receipt["problems"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n对照集回执:{out}")
    return 0 if receipt["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
