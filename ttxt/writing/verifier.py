"""L4 事实核验层(批次4,确定性规则,不调用模型)。

替换 P6/P10 的 bigram overlap≥0.2 启发式证据链:

1. 逐事实绑定:每条 claim 绑定来源文章 id + 原始 URL + **原文连续片段**
   (quote 必为 source_text[start:end] 的连续切片,offset 入库可复核);
2. 分维度核验:数字 / 日期 / 人物 / 机构 / 因果 各自独立提取并在来源全文中
   逐一验证,缺失记入 checks.missing,绝不合并成单一分数;
3. 事实-观点分离:观点句(我认为/显然/预计…)不进入事实核验,单独记录;
4. 标题-正文一致性(标题兑现):标题中的数字/日期/人物/机构与词面覆盖必须在
   正文出现;
5. 启发式边界(必须如实记录):人物/机构提取依赖后缀与谓词线索,存在漏检与
   误检;因果仅验证「连接词与绑定引句共现」,不验证因果方向。规则不能证明
   事实为真,只能证明「草稿陈述与来源文本一致」;与来源本身是否为真无关。

credibility 语义(门禁据此判定):
- verified     :引句绑定成功且所有维度锚点都在来源中找到;
- unverified   :锚点缺失或引句重叠过低(不通过);
- no_source    :claim 无有效来源编号(不通过);
- opinion      :观点句,不参与事实门禁,单独记录。
   已知边界:句中出现任一 hedge 词即整句路由到观点(即使含数字锚点);
   hedge 词表刻意收紧过「个人/评论」这类高碰撞词,但仍可能漏检其他变体。
"""

from __future__ import annotations

import re

from ttxt.crawler.simhash import tokenize

QUOTE_MAX_CHARS = 220
QUOTE_MIN_OVERLAP = 0.34  # 引句与 claim 的词面重叠下限(低于此视为未绑定)

# 长度保持的全角→半角归一(1 码点换 1 码点,offset 不错位)
_FW = str.maketrans(
    {
        **{chr(0xFF10 + i): chr(0x30 + i) for i in range(10)},
        "％": "%",
        "．": ".",
        "０": "0",
        "　": " ",
    }
)

NUMBER_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|亿元?|万美元|美元|万元|元|欧元|日元|英镑|个百分点|人次|人|名|家|款|次|岁|辆|架|部|吨|倍)?",
    re.UNICODE,
)
CN_NUMBER_RE = re.compile(r"[一二三四五六七八九十百千万亿两]{1,8}(?:万|亿|千)?(?:余|多|左右)?")
DATE_RE = re.compile(
    r"\d{4}年\d{1,2}月(?:\d{1,2}日)?|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}日|\d{4}年"
)
PERSON_RE = re.compile(
    r"([\u4e00-\u9fff]{2,4})(?=表示|称|说|指出|强调|透露|介绍|回应|告诉|预计|认为|称)"
)
ORG_RE = re.compile(
    r"[\u4e00-\u9fff]{2,8}?(?:公司|集团|研究院|研究所|科学院|大学|学院|银行|委员会|协会|学会|机构|部门|医院|学校|总局|总部|组织|部委|基金会)"
)
CAUSAL_RE = re.compile(r"因为|由于|导致|因此|所以|使得|致使|引发|造成|促使|带动|使得")
OPINION_RE = re.compile(
    r"我认为|我们认为|笔者|我个人|个人看来|就个人|显然|据称|据说|传闻|网友|点评|评论员"
    r"|预计|或将|可能|或许|应该|必须|建议|期待|展望|看法|感觉"
)
_SENT_END = re.compile(r"[。！？!?；;\n]")


def norm(text: str) -> str:
    return (text or "").translate(_FW)


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = norm(item).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def extract_anchors(text: str) -> dict[str, list[str]]:
    """从文本提取五类事实锚点(去重,保序)。"""
    numbers = _dedup(NUMBER_RE.findall(norm(text)))
    # 纯中文数量词只在与阿拉伯数字不重叠时补充,避免「三大」与「3」双计
    arabic = "".join(numbers)
    cn = [item for item in _dedup(CN_NUMBER_RE.findall(text)) if item not in arabic]
    return {
        "numbers": numbers + cn,
        "dates": _dedup(DATE_RE.findall(norm(text))),
        "persons": _dedup(PERSON_RE.findall(text)),
        "orgs": _dedup(ORG_RE.findall(text)),
        "causal": _dedup(CAUSAL_RE.findall(text)),
    }


def is_opinion(text: str) -> list[str]:
    return _dedup(OPINION_RE.findall(text or ""))


def _source_slices(text: str) -> list[tuple[str, int]]:
    """把来源切成连续句段 (slice, offset),每段都是原文连续切片。"""
    out: list[tuple[str, int]] = []
    start = 0
    for match in _SENT_END.finditer(text):
        end = match.start() + 1
        chunk = text[start:end]
        if chunk.strip():
            out.append((chunk, start))
        start = end
    if start < len(text) and text[start:].strip():
        out.append((text[start:], start))
    return out


def bind_quote(claim: str, source_text: str) -> tuple[str, int, float] | None:
    """返回 (连续原文引句, 原文偏移, 词面重叠);找不到达标句返回 None。"""
    claim_tokens = set(tokenize(claim))
    if not claim_tokens or not source_text:
        return None
    best: tuple[str, int, float] | None = None
    for chunk, offset in _source_slices(source_text):
        tokens = set(tokenize(chunk))
        if not tokens:
            continue
        overlap = len(claim_tokens & tokens) / len(claim_tokens)
        if best is None or overlap > best[2]:
            best = (chunk, offset, overlap)
    if best is None or best[2] < QUOTE_MIN_OVERLAP:
        return None
    chunk, offset, overlap = best
    stripped = chunk.strip()
    offset += chunk.index(stripped[: min(len(stripped), 1)] or "") if stripped else 0
    if len(stripped) > QUOTE_MAX_CHARS:
        stripped = stripped[:QUOTE_MAX_CHARS]
    return stripped, offset, round(overlap, 3)


def verify_claim(claim: str, article: dict | None) -> dict:
    """核验单条事实。article 需含 id/title/url/text。"""
    markers = is_opinion(claim)
    if markers:
        return {
            "claim": claim,
            "kind": "opinion",
            "article_id": (article or {}).get("id"),
            "source_url": (article or {}).get("url"),
            "quote": None,
            "quote_offset": None,
            "overlap": 0.0,
            "checks": {"opinion_markers": markers},
            "credibility": "opinion",
        }
    if not article:
        return {
            "claim": claim,
            "kind": "fact",
            "article_id": None,
            "source_url": None,
            "quote": None,
            "quote_offset": None,
            "overlap": 0.0,
            "checks": {},
            "credibility": "no_source",
        }
    text = article.get("text") or ""
    bound = bind_quote(claim, text)
    anchors = extract_anchors(claim)
    norm_text = norm(text)
    checks: dict = {}
    all_ok = True
    for dim in ("numbers", "dates", "persons", "orgs"):
        found = [item for item in anchors[dim] if norm(item) in norm_text]
        missing = [item for item in anchors[dim] if norm(item) not in norm_text]
        checks[dim] = {"found": found, "missing": missing}
        all_ok = all_ok and not missing
    quote_norm = norm(bound[0]) if bound else ""
    if anchors["causal"]:
        # 因果连接词必须与「绑定引句」共现(回退全文会放过无关因果句)
        supported = bool(quote_norm) and all(item in quote_norm for item in anchors["causal"])
        checks["causal"] = {
            "found": [item for item in anchors["causal"] if item in quote_norm],
            "missing": [item for item in anchors["causal"] if item not in quote_norm],
            "note": "仅验证因果连接词与绑定引句共现,不验证因果方向",
        }
        all_ok = all_ok and supported
    else:
        checks["causal"] = {"found": [], "missing": [], "note": "claim 无因果连接词"}
    if bound is None:
        credibility = "unverified"
        all_ok = False
    else:
        credibility = "verified" if all_ok else "unverified"
    return {
        "claim": claim,
        "kind": "fact",
        "article_id": article["id"],
        "source_url": article.get("url"),
        "quote": bound[0] if bound else None,
        "quote_offset": bound[1] if bound else None,
        "overlap": bound[2] if bound else 0.0,
        "anchors": anchors,
        "checks": checks,
        "credibility": credibility,
    }


def quote_verbatim(quote: str | None, offset: int | None, source_text: str) -> bool:
    """复核 quote 确为原文连续切片(验收用,不依赖模型)。"""
    if not quote or offset is None or source_text is None:
        return False
    return source_text[offset : offset + len(quote)] == quote


def check_title(title: str, body: str) -> dict:
    """标题兑现:标题锚点与词面覆盖必须在正文出现。"""
    anchors = extract_anchors(title)
    body_norm = norm(body)
    missing = {
        dim: [item for item in anchors[dim] if norm(item) not in body_norm]
        for dim in ("numbers", "dates", "persons", "orgs")
    }
    title_tokens = set(tokenize(title))
    body_tokens = set(tokenize(body))
    coverage = (
        len(title_tokens & body_tokens) / len(title_tokens) if title_tokens else 1.0
    )
    ok = not any(missing.values()) and coverage >= 0.5
    return {"ok": ok, "missing": {k: v for k, v in missing.items() if v}, "coverage": round(coverage, 3)}


def deterministic_findings(evidence: list[dict], title: str, body: str) -> tuple[list[str], list[str]]:
    """把核验结果转成门禁条目;确定性 critical 不依赖模型评审。"""
    issues: list[str] = []
    critical: list[str] = []
    facts = [item for item in evidence if item.get("kind") == "fact"]
    opinions = [item for item in evidence if item.get("kind") == "opinion"]
    for item in facts:
        if item["credibility"] == "no_source":
            critical.append(f"无来源断言:{item['claim'][:50]}")
            continue
        if item["credibility"] != "verified":
            missing = {
                dim: payload.get("missing") or []
                for dim, payload in (item.get("checks") or {}).items()
                if dim in ("numbers", "dates", "persons", "orgs", "causal") and payload.get("missing")
            }
            detail = ";".join(f"{dim}:{','.join(vals)}" for dim, vals in missing.items())
            critical.append(
                f"事实未通过来源核验:{item['claim'][:50]}" + (f"({detail})" if detail else "(原文引句未绑定)")
            )
    if facts and len(opinions) > len(facts):
        issues.append("正文以观点为主,可核验事实锚点不足")
    title_check = check_title(title, body)
    if not title_check["ok"]:
        parts = [f"{dim}:{','.join(vals)}" for dim, vals in title_check["missing"].items()]
        parts.append(f"标题词面正文覆盖 {title_check['coverage']:.0%}")
        issues.append("标题-正文一致性(标题兑现):" + ";".join(parts))
    return issues, critical
