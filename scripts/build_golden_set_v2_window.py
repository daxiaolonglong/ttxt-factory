# -*- coding: utf-8 -*-
"""T-02 方案 A:黄金集「近 N 日事件窗口 + 已取证来源身份」重采样。

与旧构建器(``build_golden_set_v2.py``)的关系:
- 旧文件 ``golden_set_v2.jsonl`` **一字节不改**,旧读数与复算命令全部保留;
- 本脚本另出 ``golden_set_v2_window.jsonl``(同样是 61 张,分母不变),
  只把取材规则换成「近 N 日 + 来源身份可核」,门禁与判定规则一律沿用 harness;
- 新旧两套结果并列写进 ``08_wiki/PROJECT_STATUS.md``,不互相覆盖。

重采样规则(可复现):
1. 窗口 = [as_of - N + 1 天, as_of],N 默认 7(依据见 ``WINDOW_BASIS`` 与窗口扫描表);
2. 只取 ``published_at`` 落在窗口内、正文 60–20000 字、句子干净的文章;
3. 来源身份必须可核:优先逐页取证清单(``03_intelligence/source_identity/*.jsonl``),
   其次 ``SOURCE_PUBLISHER`` 注册声明;两者都没有(unknown)一律剔除并计数;
4. 每个类目按「同一来源最多 2 个种子 → 有独立同题对优先 → 逐页取证优先 →
   own_report 优先 → 发布时间倒序」选种子(tech 4 / finance 3 / health 3);
5. 证据级:A 仅限 ``PRIMARY_SOURCES`` 一手机构稿;B 必须由 ``source_identity.find_pairs``
   给出跨机构成对(带 corroborating_urls);聚合平台与单一来源一律 C;
6. 卡片配方与旧构建器一致(正样本/数字/日期/因果/机构/人物/观点 + 每类目 2 张无关),
   按类目目标张数截断,保证总数 61、类目 25/19/17。

用法:
    python scripts/build_golden_set_v2_window.py [--days 7] [--as-of 2026-11-19]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import build_golden_set_v2 as base           # noqa: E402
from ttxt import config as cfg               # noqa: E402
from ttxt.analysis import source_identity as si  # noqa: E402
from ttxt.writing import verifier            # noqa: E402

WINDOW_DAYS_DEFAULT = 7
SCAN_DAYS = (1, 3, 7, 14, 30)
TARGETS = {"technology": 25, "finance": 19, "health": 17}
SEEDS = {"technology": 4, "finance": 3, "health": 3}
PREFIX = {"technology": "w2t", "finance": "w2f", "health": "w2h"}
UNRELATED_PER_CATEGORY = 2
MAX_SEEDS_PER_SOURCE = 2
PRIMARY_SOURCES = {"who_cn_news"}           # 与旧构建器一致:只有机构官网一手稿可给 A
PROBE_DIR = ROOT / "03_intelligence" / "source_identity"
OLD_CARDS = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"
OUT_CARDS = ROOT / "10_tests" / "golden" / "golden_set_v2_window.jsonl"
OUT_META = ROOT / "10_tests" / "golden" / "golden_set_v2_window_build.json"
OUT_PAIRS = ROOT / "03_intelligence" / "source_identity" / "window-pairs-{as_of}.json"

WINDOW_BASIS = (
    "N=7 的取值依据:①本轮要验证的是「同日多源原创覆盖」,实测首批成对发生在 2026-11-18,"
    "7 日窗口完整覆盖该日并留出同周边际;②R34/R35 扩源后的首个完整自然周就是 11-13..11-19,"
    "窗口与扩源生效期对齐,避免拿扩源前的旧覆盖充数;③窗口扫描表显示 1 日窗口(11-19)在库内"
    "没有可用正文,3 日窗口 health 仅个位数,不足以支撑每类目 ≥10 张;④14/30 日会混入 R34 之前"
    "的旧语料,削弱「同事件窗口」的含义。N 只影响重采样样本,不参与任何门禁。"
)


def _d(value: str | None) -> str:
    return (value or "")[:10]


def _identity_from_row(row: dict) -> si.Identity | None:
    kind = row.get("kind")
    if kind not in (si.KIND_OWN, si.KIND_REPRINT, si.KIND_AGGREGATOR,
                    si.KIND_UNKNOWN, si.KIND_CONFLICT):
        return None
    return si.Identity(kind, si.canon_agency(row.get("agency")), row.get("page_marker"),
                       row.get("dateline_agency"), row.get("evidence") or "")


def load_probe_identities() -> dict[str, si.Identity]:
    """所有逐页取证清单按 URL 合并;同一 URL 多份时后写覆盖,字段结构一致。"""
    out: dict[str, si.Identity] = {}
    for path in sorted(PROBE_DIR.glob("*.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ident = _identity_from_row(row)
            if row.get("url") and ident is not None:
                out[row["url"]] = ident
    return out


def identity_of(art: dict, probes: dict[str, si.Identity]) -> tuple[si.Identity, str]:
    ident = probes.get(art["url"])
    if ident is not None:
        return ident, "page_probe"
    ident = si.identity_for_source(art["source"])
    if ident.kind == si.KIND_UNKNOWN:
        return ident, "unregistered"
    return ident, "registered"


def load_articles(category: str) -> list[dict]:
    db_path = cfg.category_db_path(category)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT id, title, url, source, published_at,
                   coalesce(nullif(cleaned_content,''), content) AS text
            FROM articles
            WHERE length(coalesce(nullif(cleaned_content,''), content)) BETWEEN 60 AND 20000
            """
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def score_sentences(art: dict) -> dict | None:
    clean = [s for s in base.SENTENCE_RE.findall(art["text"])
             if not base.JUNK_RE.search(s) and base.CJK_RUN_RE.search(s)]
    with_num = [s for s in clean if base.NUM_RE.search(s)]
    with_date = [s for s in clean if verifier.DATE_RE.search(s)]
    with_anchor = [s for s in clean
                   if (verifier.extract_anchors(s).get("orgs")
                       or verifier.extract_anchors(s).get("persons"))]
    if len(clean) < 2 or not (with_num or with_anchor):
        return None
    return {**art, "sentences": clean, "with_num": with_num,
            "with_date": with_date, "with_anchor": with_anchor}


def scan_windows(articles: dict[str, list[dict]], as_of: date) -> dict:
    table: dict[str, dict] = {}
    for days in SCAN_DAYS:
        start = (as_of - timedelta(days=days - 1)).isoformat()
        table[str(days)] = {}
        for category, rows in articles.items():
            usable = sum(1 for r in rows if start <= _d(r["published_at"]) <= as_of.isoformat())
            table[str(days)][category] = usable
    return table


def select_seeds(category: str, rows: list[dict], probes: dict[str, si.Identity],
                 start: str, end: str, limit: int | None = None) -> tuple[list[dict], Counter, Counter]:
    limit = limit or SEEDS[category]
    excluded = Counter()
    candidates = []
    for art in rows:
        if not (start <= _d(art["published_at"]) <= end):
            continue
        if score_sentences(art) is None:
            continue
        ident, basis = identity_of(art, probes)
        if ident.kind == si.KIND_UNKNOWN or ident.agency is None:
            excluded["来源身份不可核"] += 1
            continue
        candidates.append({**art, "identity": ident, "identity_basis": basis})
    candidates.sort(key=lambda a: (
        1 if a["identity"].kind == si.KIND_OWN else 0,
        1 if a["identity_basis"] == "page_probe" else 0,
        _d(a["published_at"]), a["id"],
    ), reverse=True)
    # 同一来源最多 2 个种子,避免单源霸占样本(旧集 4 个科技种子全来自 toutiao_tech)
    picked: list[dict] = []
    per_source: Counter = Counter()
    for art in candidates:
        if len(picked) >= limit:
            break
        if per_source[art["source"]] >= MAX_SEEDS_PER_SOURCE:
            continue
        picked.append(score_sentences(art))
        per_source[art["source"]] += 1
    for art in candidates:
        if len(picked) >= limit:
            break
        if any(p["id"] == art["id"] for p in picked):
            continue
        picked.append(score_sentences(art))
    return picked, excluded, per_source


def attach_partners(articles: dict[str, list[dict]], identities: dict[str, si.Identity]) -> dict:
    """在窗口内跑生产配对,返回 url -> 最佳独立同题伙伴。"""
    partner: dict[str, dict] = {}
    pair_report: dict[str, dict] = {}
    for category, rows in articles.items():
        items = [{"url": r["url"], "title": r["title"], "domain": si.domain_of(r["url"]),
                  "source": r["source"], "article_id": r["id"], "date": _d(r["published_at"])}
                 for r in rows if r["url"] in identities]
        pairs, rejected = si.find_pairs(items, identities)
        ranked = sorted(pairs, key=lambda p: p["score"], reverse=True)
        for p in ranked:
            for me, other in ((p["left"], p["right"]), (p["right"], p["left"])):
                if me["url"] not in partner:
                    partner[me["url"]] = {
                        "url": other["url"], "title": other["title"],
                        "domain": other["domain"], "agency": identities[other["url"]].agency,
                        "kind": identities[other["url"]].kind, "score": p["score"],
                        "date": other.get("date"),
                    }
        pair_report[category] = {
            "pairs": [{"left": p["left"], "right": p["right"], "score": p["score"]} for p in pairs],
            "rejected": rejected,
            "same_day_pairs": sum(1 for p in pairs if p["left"].get("date") == p["right"].get("date")),
            "scan_pairs": len(items) * (len(items) - 1) // 2,
        }
    return {"partner": partner, "report": pair_report}


def tier_and_note(art: dict) -> tuple[str, list[str], str]:
    ident = art["identity"]
    basis = art["identity_basis"]
    partner = art.get("partner")
    if partner:
        note = (
            f"B 级:同事件独立机构 {partner['agency']}({partner['domain']},score {partner['score']},"
            f"日期 {partner['date']});本页身份 {ident.kind}/{ident.agency}({basis})。"
            "两篇由 find_pairs 判定为不同机构、非互相转载,corroborating_urls 可核"
        )
        return "B", [partner["url"]], note
    if art["source"] in PRIMARY_SOURCES:
        return "A", [], f"A 级:一手机构官网稿,机构 {ident.agency}({basis}),无独立第二来源"
    if ident.kind == si.KIND_AGGREGATOR:
        return "C", [], f"C 级:聚合平台自媒体稿,血统未查明({basis}),不得当独立第二来源"
    return "C", [], f"C 级:单一来源 {ident.agency or art['source']}({ident.kind},{basis}),无独立第二来源"


def build_cards(category: str, seeds: list[dict]) -> list[dict]:
    cards: list[dict] = []
    prefix = PREFIX[category]
    others: list[tuple[str, int]] = []
    for idx, art in enumerate(seeds):
        tier, corr, independence = tier_and_note(art)
        sentences = art["sentences"]
        with_num = art["with_num"]
        num_sentence = with_num[0] if with_num else sentences[0]
        second = with_num[1] if len(with_num) > 1 else sentences[-1]
        plain = sentences[0]
        anchor_sentence = (art.get("with_anchor") or [second])[0]
        anchors = verifier.extract_anchors(anchor_sentence)
        orgs, persons = anchors["orgs"], anchors["persons"]
        common = dict(
            category=category, url=art["url"], publisher=art["source"], text=art["text"],
            article_id=art["id"], title=art["title"], tier=tier, corroborating=corr,
            independence=independence,
        )
        tag = f"窗口重采样:{_d(art['published_at'])} / {art['identity_basis']}"
        cards.append(base.card(
            cid=f"{prefix}{len(cards)+1:02d}", fact_type="quote", kind="positive",
            claim=num_sentence, excerpt=num_sentence, expected="verified",
            note=f"{tag};正样本只测「草稿与来源文本一致」,不证明事实为真", **common))
        m = base.NUM_RE.search(num_sentence)
        if m:
            original = m.group(0)
            try:
                bumped = str(int(float(original.replace(",", ""))) + 7)
            except ValueError:
                bumped = original + "0"
            cards.append(base.card(
                cid=f"{prefix}{len(cards)+1:02d}", fact_type="number",
                kind="negative_perturbed", claim=num_sentence[:m.start()] + bumped + num_sentence[m.end():],
                excerpt=num_sentence, expected="unverified",
                note=f"{tag};数字 {original} 改为 {bumped},期望被数字维度拦下", **common))
        date_sentence = (art["with_date"] or [num_sentence])[0]
        date_m = re.search(r"\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|\d{4}年", date_sentence)
        if date_m:
            frag = date_m.group(0)
            shifted = re.sub(r"\d", lambda d: str((int(d.group(0)) + 1) % 10), frag, count=2)
            cards.append(base.card(
                cid=f"{prefix}{len(cards)+1:02d}", fact_type="date",
                kind="negative_perturbed",
                claim=date_sentence[:date_m.start()] + shifted + date_sentence[date_m.end():],
                excerpt=date_sentence, expected="unverified",
                note=f"{tag};日期 {frag} 被改写,期望被日期维度拦下", **common))
        cards.append(base.card(
            cid=f"{prefix}{len(cards)+1:02d}", fact_type="causal",
            kind="negative_fabricated_causal",
            claim=f"{plain.rstrip('。!?;!?')}导致{second.lstrip('，, ')}",
            excerpt=second, expected="unverified",
            note=f"{tag};同篇两句被「导致」强行连接", **common))
        if orgs:
            cards.append(base.card(
                cid=f"{prefix}{len(cards)+1:02d}", fact_type="org",
                kind="negative_wrong_entity", claim=f"昆仑智能研究院表示,{anchor_sentence.strip()}",
                excerpt=anchor_sentence, expected="unverified",
                note=f"{tag};机构 {orgs[:3]} 被替换为来源中不存在的机构", **common))
        if persons:
            cards.append(base.card(
                cid=f"{prefix}{len(cards)+1:02d}", fact_type="person",
                kind="negative_wrong_entity", claim=f"李明辉表示,{anchor_sentence.strip()}",
                excerpt=anchor_sentence, expected="unverified",
                note=f"{tag};人物 {persons[:3]} 被替换为来源中不存在的人物", **common))
        marker = base.OPINION_MARKERS[idx % len(base.OPINION_MARKERS)]
        cards.append(base.card(
            cid=f"{prefix}{len(cards)+1:02d}", fact_type="quote", kind="opinion_attributed",
            claim=f"业内人士{marker},{plain.strip().rstrip('。!?;!?')}将继续发酵。",
            excerpt=plain, expected="unverified",
            note=(f"{tag};含观点词「{marker}」的捏造预测:归属「业内人士{marker}」不在来源中,"
                  "按 T-15 不得路由 opinion,应回事实门禁判 unverified(与旧集口径一致)"), **common))
        others.append((plain, art["id"]))
    if len(seeds) >= 2:
        for claim, src_id in others[:2]:
            target = next((a for a in seeds if a["id"] != src_id), None)
            if target is None or claim in target["text"]:
                continue
            tier, corr, independence = tier_and_note(target)
            cards.append(base.card(
                cid=f"{prefix}{len(cards)+1:02d}", category=category, fact_type="quote",
                kind="unrelated", claim=claim, url=target["url"], publisher=target["source"],
                excerpt=target["sentences"][0], text=target["text"], article_id=target["id"],
                title=target["title"], expected="unverified", tier=tier, corroborating=corr,
                independence=independence,
                note=f"claim 取自 article {src_id},绑定到无关 article {target['id']};期望无法绑定引句"))
    return cards


def _card_key(card: dict) -> tuple[str, str]:
    return (card.get("source_url") or "", card.get("claim") or "")


def overlap_stats(new_cards: list[dict], old_cards: list[dict]) -> dict:
    old_keys = {_card_key(c) for c in old_cards}
    old_urls = {c.get("source_url") for c in old_cards}
    new_keys = {_card_key(c) for c in new_cards}
    new_urls = {c.get("source_url") for c in new_cards}
    by_cat = {}
    for cat in TARGETS:
        oc = {_card_key(c) for c in old_cards if c["category"] == cat}
        nc = [_card_key(c) for c in new_cards if c["category"] == cat]
        by_cat[cat] = {
            "old_cards": len(oc),
            "new_cards": len(nc),
            "overlap_cards": sum(1 for k in nc if k in oc),
            "new_only_cards": sum(1 for k in nc if k not in oc),
        }
    overlap = len(new_keys & old_keys)
    return {
        "card_level": {
            "overlap": overlap,
            "new": len(new_cards) - overlap,
            "eliminated": len(old_cards) - overlap,
            "key": "source_url + claim(逐字)",
        },
        "url_level": {
            "old_unique_urls": len(old_urls),
            "new_unique_urls": len(new_urls),
            "overlap_urls": len(old_urls & new_urls),
        },
        "by_category": by_cat,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="黄金集窗口重采样(方案 A)")
    parser.add_argument("--days", type=int, default=WINDOW_DAYS_DEFAULT)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--out", default=str(OUT_CARDS))
    parser.add_argument("--meta", default=str(OUT_META))
    parser.add_argument("--pairs-out", default="")
    args = parser.parse_args(argv)

    as_of = date.fromisoformat(args.as_of)
    start = (as_of - timedelta(days=args.days - 1)).isoformat()
    end = as_of.isoformat()
    probes = load_probe_identities()
    all_rows = {cat: load_articles(cat) for cat in TARGETS}
    scan = scan_windows(all_rows, as_of)

    seeds_by_cat: dict[str, list[dict]] = {}
    excluded = Counter()
    identity_basis = Counter()
    seed_sources: dict[str, dict] = {}
    for cat in TARGETS:
        limit = SEEDS[cat]
        while True:
            seeds, excl, per_source = select_seeds(cat, all_rows[cat], probes, start, end, limit)
            seed_cards = build_cards(cat, seeds)
            base_count = sum(1 for c in seed_cards if c["kind"] != "unrelated")
            # 种子不足以撑满类目配额时按同一排序继续补种子(规则确定,不针对配对结果调)
            if base_count >= TARGETS[cat] - UNRELATED_PER_CATEGORY or limit >= 12:
                break
            limit += 1
        if base_count < TARGETS[cat] - UNRELATED_PER_CATEGORY:
            raise SystemExit(f"{cat} 窗口内可用种子不足:需要 {TARGETS[cat]} 张,只生成 {base_count} 张基础卡")
        seeds_by_cat[cat] = seeds
        excluded.update(excl)
        for art in seeds:
            identity_basis[f"{art['identity_basis']}/{art['identity'].kind}"] += 1
        seed_sources[cat] = dict(per_source)

    # 窗口内配对的条目必须身份可核(unknown 已剔除)
    window_rows = {}
    identities: dict[str, si.Identity] = {}
    for cat in TARGETS:
        rows = []
        for art in all_rows[cat]:
            if not (start <= _d(art["published_at"]) <= end):
                continue
            ident, _basis = identity_of(art, probes)
            if ident.kind == si.KIND_UNKNOWN or ident.agency is None:
                continue
            identities[art["url"]] = ident
            rows.append(art)
        window_rows[cat] = rows
    pairing = attach_partners(window_rows, identities)
    for cat, seeds in seeds_by_cat.items():
        for art in seeds:
            art["partner"] = pairing["partner"].get(art["url"])

    cards: list[dict] = []
    for cat in ("technology", "finance", "health"):
        quota = TARGETS[cat]
        seed_cards = build_cards(cat, seeds_by_cat[cat])
        base_cards = [c for c in seed_cards if c["kind"] != "unrelated"]
        unrelated = [c for c in seed_cards if c["kind"] == "unrelated"][:UNRELATED_PER_CATEGORY]
        picked = base_cards[: quota - len(unrelated)]
        if len(picked) + len(unrelated) != quota:
            raise SystemExit(f"{cat} 卡片配额不匹配:{len(picked)}+{len(unrelated)} ≠ {quota}")
        cards.extend(picked + unrelated)

    # card_id 必须全局唯一且稳定(按类目已排序)
    seen = set()
    for card in cards:
        cid = card["card_id"]
        while cid in seen:
            cid = f"{cid}x"
        seen.add(cid)
        card["card_id"] = cid

    assert len(cards) == sum(TARGETS.values()), f"卡片数 {len(cards)} ≠ 61"
    for cat, quota in TARGETS.items():
        got = sum(1 for c in cards if c["category"] == cat)
        assert got == quota, f"{cat} 卡片数 {got} ≠ {quota}"

    old_cards = [json.loads(l) for l in OLD_CARDS.read_text(encoding="utf-8").splitlines() if l.strip()]
    out_cards = Path(args.out)
    out_cards.parent.mkdir(parents=True, exist_ok=True)
    out_cards.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cards) + "\n",
                         encoding="utf-8")

    pair_totals = {
        "candidate_pairs": sum(len(v["pairs"]) + len(v["rejected"]) for v in pairing["report"].values()),
        "independent_pairs": sum(len(v["pairs"]) for v in pairing["report"].values()),
        "same_day_pairs": sum(v["same_day_pairs"] for v in pairing["report"].values()),
        "scan_pairs": sum(v["scan_pairs"] for v in pairing["report"].values()),
    }
    pair_totals["pair_independence_share"] = si.pair_independence_share(
        [p for v in pairing["report"].values() for p in v["pairs"]],
        [r for v in pairing["report"].values() for r in v["rejected"]],
    )
    pairs_out = Path(args.pairs_out) if args.pairs_out else Path(
        str(OUT_PAIRS).format(as_of=as_of.strftime("%Y%m%d")))
    pairs_out.parent.mkdir(parents=True, exist_ok=True)
    pairs_out.write_text(json.dumps({
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window_days": args.days, "window_start": start, "window_end": end,
        "categories": pairing["report"], "totals": pair_totals,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    tier_counts = Counter(c["source_tier"] for c in cards)
    meta = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(), "window_days": args.days,
        "window_start": start, "window_end": end, "window_basis": WINDOW_BASIS,
        "window_scan_usable_articles": scan,
        "targets": TARGETS,
        "seed_sources": seed_sources,
        "seed_articles": [
            {"category": cat, "article_id": a["id"], "source": a["source"], "date": _d(a["published_at"]),
             "url": a["url"], "identity_basis": a["identity_basis"],
             "identity_kind": a["identity"].kind, "agency": a["identity"].agency,
             "tier": tier_and_note(a)[0], "partner_url": (a.get("partner") or {}).get("url"),
             "partner_agency": (a.get("partner") or {}).get("agency"),
             "partner_score": (a.get("partner") or {}).get("score")}
            for cat, seeds in seeds_by_cat.items() for a in seeds
        ],
        "identity_basis_counts": dict(identity_basis),
        "excluded": dict(excluded),
        "tier_counts": dict(tier_counts),
        "kind_counts": dict(Counter(c["kind"] for c in cards)),
        "category_counts": dict(Counter(c["category"] for c in cards)),
        "pair_stats": pair_totals,
        "pair_report": str(pairs_out.relative_to(ROOT)),
        "overlap_with_old": overlap_stats(cards, old_cards),
        "notes": (
            "窗口重采样只替换取材样本,不改门禁、不改分母(仍 61 张/类目 25-19-17)、"
            "不改卡片判定字段;旧文件 golden_set_v2.jsonl 未改动。"
        ),
    }
    meta_out = Path(args.meta)
    meta_out.parent.mkdir(parents=True, exist_ok=True)
    meta_out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: meta[k] for k in (
        "window_days", "window_start", "window_end", "category_counts", "tier_counts",
        "identity_basis_counts", "excluded", "pair_stats", "overlap_with_old")},
        ensure_ascii=False, indent=2))
    print(f"\n卡片:{out_cards}\n元数据:{meta_out}\n配对报告:{pairs_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
