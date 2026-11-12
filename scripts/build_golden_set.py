"""P12 批次4:黄金核验集构建器(确定性,读真实语料,不调用模型)。

产出两份文件:
1. 10_tests/golden/golden_set_v1.json —— 候选卡片 + 机器预裁结果;
2. 08_wiki/reports/P12-batch4-golden-review.md —— 人工复核清单。

诚实边界:
- 「构造预期」只是按构造方式推定的标签,不是人工裁定;
- 所有卡片 human_adjudicated=false,直到用户逐条回复通过/驳回;
- 机器预裁一致率只说明规则与构造意图的吻合度,不代表规则正确性,
  更不构成「事实核查」能力结论。
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttxt.crawler import store  # noqa: E402
from ttxt.writing import verifier  # noqa: E402

CATEGORIES = ("technology", "finance", "health")
SENT_SPLIT = re.compile(r"(?<=[。!?！？])|(?<=\n)")
PERSON_NAMES = ["张伟", "李慧敏", "王建国", "赵岩", "陈慕言", "刘子墨", "周砚"]
ORG_NAME = "昆仑智能研究院"

QUOTAS = {
    "positive": 16,
    "number": 8,
    "date": 6,
    "person": 6,
    "org": 5,
    "causal": 6,
    "unrelated": 6,
    "opinion": 4,
}


def load_articles(category: str) -> list[dict]:
    db_path = store.category_db_path(category) if hasattr(store, "category_db_path") else None
    if db_path is None:
        from ttxt import config as cfg

        db_path = cfg.category_db_path(category)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT id, title, url, coalesce(cleaned_content, content) AS text "
            "FROM articles WHERE coalesce(cleaned_content, content) IS NOT NULL "
            "ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    out = []
    for row in rows:
        text = (row[3] or "").strip()
        if 200 <= len(text) <= 8000:
            out.append({"id": row[0], "title": row[1] or "", "url": row[2], "text": text, "category": category})
    return out


def sentences(article: dict) -> list[str]:
    out: list[str] = []
    for raw in SENT_SPLIT.split(article["text"]):
        s = raw.strip().strip(",,;;: :)】」』")
        if 15 <= len(s) <= 80 and "\n" not in s:
            out.append(s)
    return out


def perturb_number(sentence: str, full_text: str) -> str | None:
    """替换句中首个阿拉伯数字(+7/+13/+101),要求扰动值不出现于全文。"""
    for m in verifier.NUMBER_RE.finditer(verifier.norm(sentence)):
        value_m = re.match(r"\d+", m.group(0))
        if not value_m:
            continue
        value = int(value_m.group(0))
        unit = m.group(0)[len(value_m.group(0)) :]
        normed = verifier.norm(sentence)
        for delta in (7, 13, 101, 3):
            candidate = str(value + delta)
            if candidate not in full_text and f"{candidate}.{value % 10}" not in full_text:
                start, end = m.span()
                return normed[:start] + candidate + unit + normed[end:]
    return None


def perturb_date(sentence: str, full_text: str) -> str | None:
    m = verifier.DATE_RE.search(verifier.norm(sentence))
    if not m or not m.group(0).startswith("20"):
        return None
    token = m.group(0)
    year = int(token[:4])
    normed = verifier.norm(sentence)
    for delta in (1, 2, 5):
        candidate = str(year - delta)
        if candidate not in full_text:
            return normed[: m.start()] + token.replace(token[:4], candidate, 1) + normed[m.end() :]
    return None


def main() -> int:
    rng = random.Random(42)
    cards: list[dict] = []
    corpus: dict[str, list[dict]] = {cat: load_articles(cat) for cat in CATEGORIES}
    stats = {cat: len(v) for cat, v in corpus.items()}
    seq = 0

    def add(kind: str, expected: str, article: dict, claim: str, source: dict | None, note: str) -> None:
        nonlocal seq
        seq += 1
        src = source or article
        ev = verifier.verify_claim(claim, {"id": src["id"], "title": src["title"], "url": src["url"], "text": src["text"]})
        missing = {
            dim: payload["missing"]
            for dim, payload in ev["checks"].items()
            if isinstance(payload, dict) and payload.get("missing")
        }
        cards.append(
            {
                "card_id": f"g{seq:03d}",
                "kind": kind,
                "expected_by_construction": expected,
                "category": src["category"],
                "article_id": src["id"],
                "source_url": src["url"],
                "claim": claim,
                "source_excerpt": src["text"][:120],
                "machine": {
                    "credibility": ev["credibility"],
                    "quote": ev["quote"],
                    "quote_offset": ev["quote_offset"],
                    "overlap": ev["overlap"],
                    "missing": missing,
                    "agrees_with_construction": ev["credibility"] == expected,
                },
                "note": note,
                "human_adjudicated": False,
                "human_verdict": None,
            }
        )

    pool = [a for cat in CATEGORIES for a in corpus[cat]]

    # 正例:来源原句直接作为 claim
    for article in pool:
        if len([c for c in cards if c["kind"] == "positive"]) >= QUOTAS["positive"]:
            break
        for s in sentences(article):
            anchors = verifier.extract_anchors(s)
            if not any(anchors[d] for d in ("numbers", "dates")):
                continue
            add("positive", "verified", article, s, None, "来源原句(含数字/日期锚点)")
            break

    for article in pool:
        if len([c for c in cards if c["kind"] == "number"]) >= QUOTAS["number"]:
            break
        for s in sentences(article):
            if not verifier.NUMBER_RE.search(verifier.norm(s)):
                continue
            if verifier.OPINION_RE.search(s):
                continue
            claim = perturb_number(s, article["text"])
            if claim:
                add("number", "unverified", article, claim, None, "句中数字被扰动(+7/+13/+101)")
                break

    for article in pool:
        if len([c for c in cards if c["kind"] == "date"]) >= QUOTAS["date"]:
            break
        for s in sentences(article):
            if verifier.OPINION_RE.search(s):
                continue
            claim = perturb_date(s, article["text"])
            if claim:
                add("date", "unverified", article, claim, None, "句中日期年份被前移")
                break

    name_iter = iter(PERSON_NAMES)
    for article in pool:
        if len([c for c in cards if c["kind"] == "person"]) >= QUOTAS["person"]:
            break
        for s in sentences(article):
            if verifier.PERSON_RE.search(s):
                continue
            try:
                name = next(name_iter)
            except StopIteration:
                name = rng.choice(PERSON_NAMES)
            if name in article["text"]:
                continue
            add("person", "unverified", article, f"{name}表示,{s}", None, "句前挂虚构人物")
            break

    for article in pool:
        if len([c for c in cards if c["kind"] == "org"]) >= QUOTAS["org"]:
            break
        for s in sentences(article):
            if ORG_NAME in article["text"]:
                continue
            add("org", "unverified", article, f"{ORG_NAME}称,{s}", None, "句前挂虚构机构")
            break

    for article in pool:
        if len([c for c in cards if c["kind"] == "causal"]) >= QUOTAS["causal"]:
            break
        if verifier.CAUSAL_RE.search(article["text"]):
            continue  # 全文含因果连接词的语料不适合该构造,保持过滤条件与规则同向
        for s in sentences(article):
            if verifier.OPINION_RE.search(s):
                continue
            add("causal", "unverified", article, f"由于{s}", None, "无因果来源被强加「由于」")
            break

    unrelated_claims = 0
    for i, article in enumerate(pool):
        if unrelated_claims >= QUOTAS["unrelated"]:
            break
        for s in sentences(article):
            anchors = verifier.extract_anchors(s)
            if not any(anchors[d] for d in ("numbers", "dates")):
                continue
            other = next(
                (
                    b
                    for b in pool
                    if b["url"] != article["url"] and all(a not in verifier.norm(b["text"]) for a in anchors["numbers"] + anchors["dates"])
                ),
                None,
            )
            if other is None:
                continue
            add("unrelated", "unverified", article, s, other, "原句挂到不含锚点数字/日期的无关来源")
            unrelated_claims += 1
            break

    for article in pool:
        if len([c for c in cards if c["kind"] == "opinion"]) >= QUOTAS["opinion"]:
            break
        for s in sentences(article):
            if verifier.extract_anchors(s)["numbers"]:
                continue
            add("opinion", "opinion", article, f"我认为,{s}", None, "事实句加观点前缀,应走观点分离")
            break

    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = {
        "meta": {
            "version": "p12-golden-v1",
            "generated_at": generated_at,
            "corpus_articles_used": stats,
            "card_count": len(cards),
            "machine_agreement": sum(1 for c in cards if c["machine"]["agrees_with_construction"]),
            "human_adjudicated_count": 0,
            "disclaimer": "机器预裁仅是规则输出;构造预期不是人工裁定;人工逐条复核完成前,本集合不得用于宣称通过率或验收。",
        },
        "cards": cards,
    }
    out_json = ROOT / "10_tests" / "golden" / "golden_set_v1.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    sheet = [
        "# P12 批次4 黄金核验集人工复核清单(golden_set_v1)",
        "",
        f"- 生成时间:{generated_at}(机器预裁,非人工裁定)",
        f"- 卡片总数:{len(cards)};构造预期与机器预裁一致:{payload['meta']['machine_agreement']}/{len(cards)}(仅反映规则与构造意图吻合度)",
        "- **人工裁定数:0。以下每卡片 human_adjudicated=false,必须由用户逐条回复后才是黄金集。**",
        "- 回复格式:`gNNN: 通过` 或 `gNNN: 驳回`(可一次多条)。",
        "- 语义:「通过」=认可机器核验结论;「驳回」=认为机器结论错误。",
        "- 注意:规则只验证「草稿陈述与来源文本一致」,**不是真正的事实核查**。",
        "- 已知不一致来源(如实记录):claim 整句只要出现 hedge 词(可能/或将/或许等)即路由为 opinion,"
        "先于事实核验 —— 推测句不做数字/人物核验是设计边界,不是绑定错误。",
        "- 人物/机构抽取依赖后缀与谓词启发式,存在漏检与误检;因果仅验证连接词与绑定引句共现,不验证因果方向。",
        "",
        "| 卡片 | 构造类型 | 构造预期 | 机器结论 | 一致 | 缺失维度 | claim(节选) | 来源URL | 人工裁定 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in cards:
        missing = ";".join(f"{k}:{','.join(v)}" for k, v in c["machine"]["missing"].items()) or "-"
        agree = "✓" if c["machine"]["agrees_with_construction"] else "✗"
        claim = c["claim"][:46].replace("|", "\\|")
        sheet.append(
            f"| {c['card_id']} | {c['kind']} | {c['expected_by_construction']} | {c['machine']['credibility']} "
            f"| {agree} | {missing} | {claim} | {c['source_url']} | ☐ |"
        )
    sheet_path = ROOT / "08_wiki" / "reports" / "P12-batch4-golden-review.md"
    sheet_path.write_text("\n".join(sheet) + "\n", encoding="utf-8")

    print(json.dumps({"cards": len(cards), "agreement": payload["meta"]["machine_agreement"], "json": str(out_json), "sheet": str(sheet_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
