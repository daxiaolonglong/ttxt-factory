"""P13 批次1:构建黄金核验集 v2 候选卡片 + 人工复核表。

与 v1(scripts/build_golden_set.py)的关键差别:
1. 每张卡片带 source_tier / corroborating_urls / independence_note / fact_type;
2. 机器结论**不写入卡片**,由 scripts/golden_v2_harness.py 现算(避免把规则吻合度当结论);
3. 语料自带来源一律 C 级起步,只能测「① 原文匹配」,不得当作「② 事实支持」的证据;
4. A/B 级卡片只接受外部独立来源(07_tmp 的独立研究输入文件),本脚本不自动升级证据级;
5. 所有卡片 human_adjudicated=false,必须人工逐条裁定后才进入质量口径。

用法:
    python scripts/build_golden_set_v2.py [--independent 07_tmp/golden_v2_independent_candidates.json]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ttxt import config as cfg          # noqa: E402
from ttxt.writing import verifier       # noqa: E402

OUT_JSONL = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"
OUT_REVIEW = ROOT / "08_wiki" / "reports" / "P13-batch1-golden-v2-review.md"
DEFAULT_INDEPENDENT = ROOT / "07_tmp" / "golden_v2_independent_candidates.json"

SEEDS_PER_CATEGORY = {"technology": 4, "finance": 3, "health": 3}
TEXT_WINDOW = 1100          # source_text 保存 excerpt 所在窗口,保证离线复现又不臃肿
SENTENCE_RE = re.compile(r"[^。!?;!?\n]{8,120}?[。!?;!?]")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
# 清洗缺口实测:31/50 财经、30/137 科技文章的 cleaned_content 仍含 HTML 片段
# 合格句子必须含 ≥6 个连续汉字,否则 "height:8px;" / "-- EM_StockImg_Start --" 之类残片会混进卡片
JUNK_RE = re.compile(r"<[a-zA-Z/]|https?://|style=|class=|EM_|data-code")
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{6}")
# 路由实测:OPINION_RE 含 可能/应该/预计/建议,事实陈述里极常见 → 构造观点卡必须用这些词
OPINION_MARKERS = ("预计", "或将", "显然")
# 语料本身就是一手机构来源(如 WHO 官网新闻),可作 A 级证据;其余一律 C 级起步
PRIMARY_SOURCES = {"who_cn_news"}


def tier_for(source: str) -> str:
    return "A" if (source or "") in PRIMARY_SOURCES else "C"


def independence_for(source: str) -> str:
    if (source or "") in PRIMARY_SOURCES:
        return "机构官网一手稿;是否构成独立证据需人工确认(human_confirms_tier)"
    return "单一自媒体聚合稿,无独立第二来源;仅测「原文匹配」维度,不得当作事实为真的证据"


NULL_CARD_FIELDS = {
    "human_adjudicated": False,
    "human_verdict": None,
    "human_fact_support": None,
    "human_confirms_tier": None,
}


def pick_articles(category: str, limit: int) -> list[dict]:
    """取材与写作链路一致:coalesce(cleaned_content, content)(workflow.py:406)。"""
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
            ORDER BY id ASC
            """
        ).fetchall()
    finally:
        con.close()
    # 句子必须干净(排除 HTML 残渣),并统计数字与人物/机构锚点,保证各维度有素材
    scored = []
    for row in rows:
        clean_rows = [
            s for s in SENTENCE_RE.findall(row["text"])
            if not JUNK_RE.search(s) and CJK_RUN_RE.search(s)
        ]
        with_num = [s for s in clean_rows if NUM_RE.search(s)]
        with_date = [s for s in clean_rows if verifier.DATE_RE.search(s)]
        with_anchor = [
            s for s in clean_rows
            if (verifier.extract_anchors(s).get("orgs") or verifier.extract_anchors(s).get("persons"))
        ]
        if len(clean_rows) >= 2 and (with_num or with_anchor):
            scored.append({
                **dict(row), "sentences": clean_rows, "with_num": with_num,
                "with_date": with_date, "with_anchor": with_anchor,
            })
    scored = scored[: limit * 6]
    return scored[:limit]


def window(text: str, excerpt: str) -> tuple[str, int]:
    """返回包含 excerpt 的窗口文本与其在窗口内的偏移。"""
    pos = text.find(excerpt)
    if pos < 0:
        return text[:TEXT_WINDOW], 0
    start = max(0, pos - (TEXT_WINDOW - len(excerpt)) // 2)
    piece = text[start : start + TEXT_WINDOW]
    return piece, piece.find(excerpt)


def card(*, cid: str, category: str, fact_type: str, kind: str, claim: str, url: str,
         publisher: str, excerpt: str, text: str, note: str, expected: str,
         tier: str = "C", corroborating: list[str] | None = None, article_id: int | None = None,
         title: str = "", independence: str = "") -> dict:
    src, offset = window(text, excerpt)
    return {
        "card_id": cid,
        "category": category,
        "fact_type": fact_type,
        "kind": kind,
        "claim": claim,
        "source_url": url,
        "source_title": title,
        "source_publisher": publisher,
        "source_tier": tier,
        "corroborating_urls": corroborating or [],
        "independence_note": independence or independence_for(publisher),
        "source_text": src,
        "source_article_id": article_id,
        "source_excerpt": excerpt,
        "quote_offset": offset,
        "expected_by_construction": expected,
        **NULL_CARD_FIELDS,
        "note": note,
    }


def build_corpus_cards(category: str, prefix: str) -> list[dict]:
    cards: list[dict] = []
    others: list[tuple[str, str]] = []      # (claim, url) 供 unrelated 卡片跨篇绑定
    seeds = pick_articles(category, SEEDS_PER_CATEGORY[category])
    for art in seeds:
        sentences = art["sentences"]
        with_num = art["with_num"]
        num_sentence = with_num[0] if with_num else sentences[0]
        second = with_num[1] if len(with_num) > 1 else sentences[-1]
        plain = sentences[0]
        anchor_sentence = (art.get("with_anchor") or [second])[0]
        anchors = verifier.extract_anchors(anchor_sentence)
        orgs = anchors["orgs"]
        persons = anchors["persons"]

        # ① 正样本(C 级:只证明「原文匹配」,不证明事实为真)
        cards.append(card(
            cid=f"{prefix}{len(cards)+1:02d}", category=category, fact_type="quote",
            kind="positive", claim=num_sentence, url=art["url"], publisher=art["source"],
            excerpt=num_sentence, text=art["text"], article_id=art["id"], title=art["title"],
            expected="verified", tier=tier_for(art["source"]),
            note=(f"{tier_for(art['source'])} 级自证卡片:只用于测「草稿与来源文本一致」;"
                  "事实为真与否须由人工裁定 + 独立来源确认"),
        ))
        # ② 数字扰动
        m = NUM_RE.search(num_sentence)
        if m:
            original = m.group(0)
            try:
                bumped = str(int(float(original.replace(",", ""))) + 7)
            except ValueError:
                bumped = original + "0"
            perturbed = num_sentence[: m.start()] + bumped + num_sentence[m.end():]
            cards.append(card(
                cid=f"{prefix}{len(cards)+1:02d}", category=category, fact_type="number",
                kind="negative_perturbed", claim=perturbed, url=art["url"], publisher=art["source"],
                excerpt=num_sentence, text=art["text"], article_id=art["id"], title=art["title"],
                expected="unverified", tier=tier_for(art["source"]),
                note=f"数字 {original} 改为 {bumped},期望被数字维度拦下",
            ))
        # ③ 日期扰动(优先用含日期的句子,否则该维度覆盖过薄:实测仅 2/56)
        date_sentence = (art["with_date"] or [num_sentence])[0]
        date_m = re.search(r"\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|\d{4}年", date_sentence)
        if date_m:
            frag = date_m.group(0)
            shifted = re.sub(r"\d", lambda d: str((int(d.group(0)) + 1) % 10), frag, count=2)
            perturbed_d = date_sentence[: date_m.start()] + shifted + date_sentence[date_m.end():]
            cards.append(card(
                cid=f"{prefix}{len(cards)+1:02d}", category=category, fact_type="date",
                kind="negative_perturbed", claim=perturbed_d, url=art["url"], publisher=art["source"],
                excerpt=date_sentence, text=art["text"], article_id=art["id"], title=art["title"],
                expected="unverified", tier=tier_for(art["source"]), note=f"日期 {frag} 被改写,期望被日期维度拦下",
            ))
        # ④ 因果捏造(真实事故形态:把同篇两句强连成因果)
        fabricated = f"{plain.rstrip('。!?;!?')}导致{second.lstrip('，, ')}"
        cards.append(card(
                cid=f"{prefix}{len(cards)+1:02d}", category=category, fact_type="causal",
                kind="negative_fabricated_causal", claim=fabricated, url=art["url"],
                publisher=art["source"], excerpt=second, text=art["text"], article_id=art["id"],
                title=art["title"], expected="unverified", tier=tier_for(art["source"]),
                note="同篇两句被「导致」强行连接;原文未陈述该因果方向(对应 P13 报告 §6 真实事故形态)",
            ))
        # ⑤ 人物/机构张冠李戴
        if orgs:
            swapped = f"昆仑智能研究院表示,{anchor_sentence.strip()}"
            cards.append(card(
                cid=f"{prefix}{len(cards)+1:02d}", category=category, fact_type="org",
                kind="negative_wrong_entity", claim=swapped, url=art["url"], publisher=art["source"],
                excerpt=anchor_sentence, text=art["text"], article_id=art["id"], title=art["title"],
                expected="unverified", tier=tier_for(art["source"]), note=f"来源出现的机构 {orgs[:3]} 被替换为不存在于来源的机构",
            ))
        if persons:
            swapped_p = f"李明辉表示,{anchor_sentence.strip()}"
            cards.append(card(
                cid=f"{prefix}{len(cards)+1:02d}", category=category, fact_type="person",
                kind="negative_wrong_entity", claim=swapped_p, url=art["url"], publisher=art["source"],
                excerpt=anchor_sentence, text=art["text"], article_id=art["id"], title=art["title"],
                expected="unverified", tier=tier_for(art["source"]), note=f"来源出现的人物 {persons[:3]} 被替换为来源中不存在的人物",
            ))
        # ⑥ 观点卡(必须命中 OPINION_RE 才算观点路由;实测「据X报道」不会被路由,见 P13 报告)
        marker = OPINION_MARKERS[seeds.index(art) % len(OPINION_MARKERS)]
        attributed = f"业内人士{marker},{plain.strip().rstrip('。!?;!?')}将继续发酵。"
        cards.append(card(
            cid=f"{prefix}{len(cards)+1:02d}", category=category, fact_type="quote",
            kind="opinion_attributed", claim=attributed, url=art["url"], publisher=art["source"],
            excerpt=plain, text=art["text"], article_id=art["id"], title=art["title"],
            expected="opinion", tier=tier_for(art["source"]),
            note=f"含 OPINION_RE 词「{marker}」的捏造预测,期望路由为 opinion 且不得计入事实核验;"
                 "同时记录边界:事实陈述中出现 可能/应该/预计 也会被整句划为观点而绕过门禁",
        ))
        others.append((plain, art["id"]))

    # ⑦ 无关来源:把 A 篇的句子绑到 B 篇正文(claim 与绑定来源必须不同篇,否则构造预期失真)
    if len(seeds) >= 2:
        for claim, src_id in others[:2]:
            target = next((a for a in seeds if a["id"] != src_id), None)
            if target is None or claim in target["text"]:
                continue
            cards.append(card(
                cid=f"{prefix}{len(cards)+1:02d}", category=category, fact_type="quote",
                kind="unrelated", claim=claim, url=target["url"], publisher=target["source"],
                excerpt=target["sentences"][0], text=target["text"], article_id=target["id"],
                title=target["title"], expected="unverified", tier=tier_for(target["source"]),
                note=f"claim 取自 article {src_id},却绑定到无关 article {target['id']} 正文;"
                     "article_id 记录的是被绑定来源,期望无法绑定引句",
            ))
    return cards


def ingest_independent(path: Path) -> list[dict]:
    """把外部独立研究输入(必须来自真实抓取)并入 v2;不自动升级证据级。"""
    if not path.exists():
        print(f"提示:独立来源输入不存在,跳过 {path}")
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    cards = []
    for src in payload.get("cards", []):
        keep = {k: src.get(k) for k in (
            "card_id", "category", "fact_type", "kind", "claim", "source_url", "source_title",
            "source_publisher", "source_tier", "corroborating_urls", "independence_note",
            "source_excerpt", "note",
        )}
        text = src.get("source_full_text") or src.get("source_excerpt") or ""
        excerpt = src.get("source_excerpt") or ""
        src_window, offset = window(text, excerpt)
        cards.append({
            **keep,
            "source_text": src_window,
            "source_article_id": None,
            "quote_offset": offset,
            "expected_by_construction": src.get("expected_by_construction", "verified"),
            **NULL_CARD_FIELDS,
        })
    return cards


def render_review(cards: list[dict], independent_path: Path) -> str:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# P13 批次1 黄金核验集 v2 人工复核表",
        "",
        f"- 生成时间:{now}",
        f"- 候选卡片:{len(cards)};**已人工裁定:0**",
        "- 回复格式:`v2xNN: 通过` / `v2xNN: 驳回`(可一次多条);需要修正证据级时 `v2xNN: 证据级=B`。",
        "- 「通过」=认可机器核验结论;「驳回」=认为机器结论错误。",
        "- **卡片全部 human_adjudicated=false;未经人工裁定的卡片不进入任何质量口径。**",
        "- 语料自证卡片一律 C 级:它们只能测「草稿与来源文本一致」,不能证明事实为真。",
        f"- A/B 级独立来源卡片由外部研究输入并入({independent_path}),其 URL/正文须人工再核一次。",
        "- 规则边界(与 verifier 一致):hedge 词整句路由观点;人物/机构靠后缀与谓词启发式,会漏检误检;"
        "因果只验证连接词共现,不验证方向。",
        "",
        "| 卡片 | 类目 | kind | fact_type | 证据级 | 独立来源数 | 构造预期 | claim(节选) | 来源 | 人工裁定 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in cards:
        claim = (c["claim"] or "")[:70].replace("|", "∣").replace("\n", " ")
        excerpt_src = c["source_publisher"]
        lines.append(
            f"| {c['card_id']} | {c['category']} | {c['kind']} | {c['fact_type']} | {c['source_tier']} "
            f"| {len(c['corroborating_urls'])} | {c['expected_by_construction']} | {claim} "
            f"| {excerpt_src} | ☐ |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建黄金核验集 v2")
    parser.add_argument("--independent", default=str(DEFAULT_INDEPENDENT))
    parser.add_argument("--out", default=str(OUT_JSONL))
    parser.add_argument("--review", default=str(OUT_REVIEW))
    parser.add_argument("--seed", type=int, default=20261113)
    args = parser.parse_args(argv)

    random.seed(args.seed)
    cards: list[dict] = []
    for category, prefix in (("technology", "v2t"), ("finance", "v2f"), ("health", "v2h")):
        cards.extend(build_corpus_cards(category, prefix))
    independent = ingest_independent(Path(args.independent))
    cards.extend(independent)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cards) + "\n", encoding="utf-8")
    review = Path(args.review)
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(render_review(cards, Path(args.independent)), encoding="utf-8")

    from collections import Counter
    print(json.dumps({
        "cards": len(cards),
        "by_category": dict(Counter(c["category"] for c in cards)),
        "by_kind": dict(Counter(c["kind"] for c in cards)),
        "by_tier": dict(Counter(c["source_tier"] for c in cards)),
        "independent_cards": len(independent),
        "human_adjudicated": 0,
        "out": str(out),
        "review": str(review),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
