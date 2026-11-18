# -*- coding: utf-8 -*-
"""逐篇来源身份与「第二家独立媒体」判定(P30)。

口径来自本人 2026-11-16 的原文抽查(见 `08_wiki/reports/P29-adjudication-v2-and-evidence-coverage-20261116.md`
与本轮 P30 账本):**域名不等于一手来源,转载稿不能当第二家独立媒体。**
因此:
1. 每篇文章的来源身份要从页面署名(「来源:xxx」)与正文电头(「中新网X月X日电」「新华社X电」)取,不能按域名猜;
2. 页面署名与电头冲突时**电头优先**(域名让路),标成 `conflicting_byline`,不得当成本站自有报道;
3. 聚合平台(今日头条自媒体)在查清血统之前一律不算独立第二来源;
4. 身份不明一律不算独立(不假定)。

本模块只做纯函数与判定,不联网、不写库;联网取证在 `scripts/source_identity_probe.py`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

KIND_OWN = "own_report"            # 该媒体自己的报道(署名与电头一致指向它)
KIND_REPRINT = "reprint_external"  # 转载/引用外部媒体:真正的发布者是别人
KIND_UNKNOWN = "unknown"           # 页面与正文都查不到署名
KIND_CONFLICT = "conflicting_byline"  # 页面署名与电头互相矛盾
KIND_AGGREGATOR = "aggregator"     # 聚合平台自媒体(血统未查明)

#: 独立第二来源资格的身份类型(kind → 是否可作为两家之一)
ELIGIBLE_KINDS = (KIND_OWN, KIND_REPRINT)
#: 不能作为独立第二来源的身份类型
INELIGIBLE_KINDS = (KIND_UNKNOWN, KIND_CONFLICT, KIND_AGGREGATOR)

#: 页面署名/电头里的机构写法 → 规范机构 id(规范 id 相同 = 同一家,不构成独立)
AGENCY_CANON: dict[str, str] = {
    "中国新闻网": "chinanews", "中新网": "chinanews", "中新社": "chinanews", "中国新闻社": "chinanews",
    "央视新闻客户端": "cctv_news", "央视新闻": "cctv_news", "央视": "cctv_news",
    "新华社": "xinhua", "新华通讯社": "xinhua",
    "中国天气网": "weather_cn", "中国新闻周刊": "china_newsweek",
    "人民日报": "people_daily", "人民网": "people", "光明网": "gmw",
    "澎湃新闻": "thepaper", "界面新闻": "jiemian",
    "东方财富网": "eastmoney", "东方财富": "eastmoney",
    "钛媒体": "tmtpost", "爱范儿": "ifanr", "少数派": "sspai", "阮一峰博客": "ruanyifeng",
    "IT之家": "ithome",
    # T-02 扩源取证:中新网与东方财富各挂一篇同题稿,页头「来源」分别写「商务部网站」与「商务部」,
    # 实为同一份官方答问。写法不归一就会被判成两家独立机构 ⇒ 必须映射到同一 id。
    "商务部网站": "mofcom", "商务部": "mofcom",
    # R35 扩源:三家原创新闻/科技编辑部,页面署名与电头都按这套写法归一到同一 id。
    "第一财经": "yicai", "第一财经日报": "yicai",
    "每日经济新闻": "nbd", "每经": "nbd",
    "量子位": "qbitai",
    # R37 扩源:雷峰网站内没有「来源:」署名,唯一的发布证据是文末原创声明
    # 「雷峰网原创文章,未经授权禁止转载」;页面上全站统一的水印「雷峰网(公众号:雷峰网)」
    # 不构成发布证据(转载稿同样带),因此只映射机构名、不拿水印当电头。
    "雷峰网": "leiphone",
    "世界卫生组织": "who", "WHO": "who",
}
#: 注册来源 → 规范机构 id(采集侧用;toutiao_* 是聚合平台,单列)
SOURCE_PUBLISHER: dict[str, str] = {
    "eastmoney_rss": "eastmoney", "tmtpost_rss": "tmtpost", "ifanr_feed": "ifanr",
    "sspai_rss": "sspai", "ruanyifeng_atom": "ruanyifeng", "who_cn_news": "who",
    "chinanews_society": "chinanews", "chinanews_culture": "chinanews",
    "chinanews_finance": "chinanews",
    "ithome_rss": "ithome",
    # R35 扩源:三个新增来源必须在这里显式声明发布机构,否则会被当 own_report
    # 与别家真稿件配成假独立对(回归测试 test_every_registered_source_declares_its_publisher 钉住)。
    "yicai_news": "yicai", "nbd_news": "nbd", "qbitai_feed": "qbitai",
    # R37 扩源:雷峰网。identity_probe 会把「原创声明」判成电头,没有声明的判 unknown,
    # 所以这里只声明发布机构,不额外放宽(unknown 不参与独立对)。
    "leiphone_feed": "leiphone",
    "toutiao_tech": "toutiao", "toutiao_finance": "toutiao", "toutiao_history": "toutiao",
    "toutiao_regimen": "toutiao",
    "quotes_toscrape": "quotes_toscrape",
}
AGGREGATOR_AGENCIES = {"toutiao"}

_DATELINE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("中新网", re.compile(r"中新网\s*[\u4e00-\u9fa5]{1,8}\s*\d{1,2}月\d{1,2}日电")),
    ("中新社", re.compile(r"中新社\s*[\u4e00-\u9fa5]{1,8}\s*\d{1,2}月\d{1,2}日电")),
    ("新华社", re.compile(r"新华社\s*[\u4e00-\u9fa5]{0,8}电")),
    ("人民日报", re.compile(r"人民日报\s*[\u4e00-\u9fa5]{0,8}\d{1,2}月\d{1,2}日电")),
    ("央视新闻", re.compile(r"央视新闻客户端?")),
    ("中国天气网", re.compile(r"中国天气网")),
    # T-02 扩源:IT之家自有稿的电头固定写「IT之家 11 月 18 日消息」;别家转载引用时同样带这句,
    # 因此它既能证自有报道,也能把转载方正确判成 reprint(ithome),不会被域名洗白。
    ("IT之家", re.compile(r"IT\s*之家\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*消息")),
    # R35 扩源:三家新增来源的自有稿标识。只用"署名/报道"这类带动作的写法,
    # 不匹配"据第一财经报道"式引用(那是别家在引述,不等于本文由第一财经发布)。
    ("第一财经", re.compile(r"第一财经(?:日报)?(?:记者|编辑|报道)")),
    ("每日经济新闻", re.compile(r"每经(?:记者|编辑|讯)")),
    ("量子位", re.compile(r"量子位\s*[|｜]")),
    # R37 扩源:雷峰网文末原创声明。只认这条完整声明——页面上全站统一的水印
    # 「雷峰网(公众号:雷峰网)」连转载稿都带,不能当发布证据;没有声明的一律 unknown。
    ("雷峰网", re.compile(r"雷峰网原创文章[,，]?\s*未经授权禁止转载")),
]
#: 「来源:xxx」署名。前面紧跟 数/材/料/章/载/文/链 的「来源」是正文里的用语,不是页头署名;
#: 「图片来源:xxx / 供图来源:xxx / 摄影来源:xxx」写的是图片版权方(常是摄影记者或图库),
#: 不能当文章发布机构 —— R35 实测每日经济新闻有 11 篇因此被误判 conflicting_byline,
#: 机构名里还混进「祝裕」「摄图网_501609341」这类图注。
_MARKER_RE = re.compile(r"(.{0,2})?来源\s*[:：]\s*([^\s　,，。;；:：|]{2,40})")
_BAD_PREFIX = set("数材料章载文链图片供摄影")
_TITLE_STOP = re.compile(r"[\s\-—_|:!,，.。?？!、\u3000\u201c\u201d\u2018\u2019()（）\[\]【】《》\"']+")

MIN_EVENT_SCORE = 0.30      # 判定"同一件事"的阈值(实测标定见 P30 账本)
CANDIDATE_FLOOR = 0.12      # 低于此完全不列出;floor~MIN 之间进入"被拒"清单并写明理由


@dataclass(frozen=True)
class Identity:
    kind: str
    agency: str | None
    page_marker: str | None
    dateline_agency: str | None
    evidence: str

    @property
    def eligible(self) -> bool:
        return self.kind in ELIGIBLE_KINDS and self.agency is not None


def canon_agency(name: str | None) -> str | None:
    if not name:
        return None
    raw = name.strip()
    if raw in AGENCY_CANON:
        return AGENCY_CANON[raw]
    for key, cid in AGENCY_CANON.items():
        if key and (raw.startswith(key) or key in raw):
            return cid
    return raw or None


def extract_page_marker(text: str | None) -> str | None:
    """从页头文本里取「来源:xxx」的 xxx;正文里的「数据来源:」不算。"""
    if not text:
        return None
    for match in _MARKER_RE.finditer(text):
        prefix = match.group(1) or ""
        if any(ch in _BAD_PREFIX for ch in prefix):
            continue
        value = match.group(2).strip()
        if value:
            return value
    return None


def extract_dateline(text: str | None) -> str | None:
    """从正文取电头机构(「中新网 北京11月15日电」「新华社天津电」);没有返回 None。"""
    if not text:
        return None
    best: tuple[int, str] | None = None
    for agency, pattern in _DATELINE_PATTERNS:
        match = pattern.search(text)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), agency)
    return best[1] if best else None


def classify(marker: str | None, dateline: str | None, *, own_agency: str = "chinanews") -> Identity:
    """页面署名 + 电头 → 身份。**冲突时电头优先,域名让路。**"""
    marker_id = canon_agency(marker)
    dateline_id = canon_agency(dateline)
    if marker_id and dateline_id and marker_id != dateline_id:
        return Identity(KIND_CONFLICT, dateline_id, marker, dateline_id,
                        f"页面署名「{marker}」与电头「{dateline}」矛盾,按电头判定")
    agency = marker_id or dateline_id
    if agency is None:
        return Identity(KIND_UNKNOWN, None, marker, dateline, "页面与正文都没有查到署名")
    evidence = f"页面署名「{marker}」" if marker_id else f"正文电头「{dateline}」"
    kind = KIND_OWN if agency == own_agency else KIND_REPRINT
    return Identity(kind, agency, marker, dateline_id, evidence)


def identity_for_source(source_id: str) -> Identity:
    """注册来源侧的身份:只有显式声明过发布机构的来源才算自有报道。

    未注册来源(例如迁移进来的 ``legacy_factory_search_ssr``)不假定是媒体,
    按「身份不明一律不算独立」返回 unknown —— R35 实测它曾与真媒体配成伪独立对,
    而且 2 字标题「对话」把相似度拉到 0.9。
    """
    agency = SOURCE_PUBLISHER.get(source_id)
    if agency is None:
        return Identity(KIND_UNKNOWN, None, None, None, f"未注册来源 {source_id},不假定为媒体")
    if agency in AGGREGATOR_AGENCIES:
        return Identity(KIND_AGGREGATOR, agency, None, None, "聚合平台自媒体,血统未查明")
    return Identity(KIND_OWN, agency, None, None, f"注册来源 {source_id}")


def independent(a: Identity, b: Identity) -> bool:
    """两家是否可作"同一件事的两个独立来源"。宁可判否,不做乐观假定。"""
    for ident in (a, b):
        if ident.agency is None or ident.kind in (KIND_UNKNOWN, KIND_CONFLICT, KIND_AGGREGATOR):
            return False
    return a.agency != b.agency


def _grams(title: str | None) -> set[str]:
    raw = _TITLE_STOP.sub("", (title or "").lower())
    if len(raw) < 2:
        return {raw} if raw else set()
    return {raw[i:i + 2] for i in range(len(raw) - 1)}


def event_score(left: dict, right: dict) -> float:
    a, b = _grams(left.get("title")), _grams(right.get("title"))
    if not a or not b:
        return 0.0
    jaccard = len(a & b) / len(a | b)
    containment = len(a & b) / min(len(a), len(b))
    return max(jaccard, containment * 0.9 if containment >= 0.6 else jaccard)


def domain_of(url: str | None) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host or "?"


def find_pairs(items: list[dict], identities: dict[str, Identity], *,
               min_score: float = MIN_EVENT_SCORE,
               floor: float = CANDIDATE_FLOOR) -> tuple[list[dict], list[dict]]:
    """在同类目条目里找"同一件事"的成对候选,并按血统规则给出成对/被拒与理由。

    items 元素需含 ``url`` / ``title``(可选 ``domain``);identities 以 url 为键。
    """
    pairs: list[dict] = []
    rejected: list[dict] = []
    for i, left in enumerate(items):
        for right in items[i + 1:]:
            score = event_score(left, right)
            if score < floor:
                continue
            pair = {"left": left, "right": right, "score": round(score, 3)}
            ident_a = identities.get(left.get("url") or "")
            ident_b = identities.get(right.get("url") or "")
            if ident_a is None or ident_b is None:
                rejected.append({**pair, "reason": "缺少来源身份记录"})
                continue
            da, db = left.get("domain") or domain_of(left.get("url")), right.get("domain") or domain_of(right.get("url"))
            if da == db:
                rejected.append({**pair, "reason": f"同一域名 {da},不构成两家独立媒体"})
            elif not independent(ident_a, ident_b):
                reason = (f"来源不独立:{ident_a.agency}/{ident_a.kind} 与 {ident_b.agency}/{ident_b.kind}")
                rejected.append({**pair, "reason": reason})
            elif score < min_score:
                rejected.append({**pair, "reason": f"标题相似度 {score:.3f} < {min_score},不足以判定同一事件"})
            else:
                pairs.append({**pair, "agencies": [ident_a.agency, ident_b.agency]})
    return pairs, rejected


def pair_independence_share(pairs: list[dict], rejected: list[dict]) -> dict:
    """只读观测:同题候选对里,通过独立性判定的占比。

    分母 = 达到 ``CANDIDATE_FLOOR`` 的同题候选对(成对 + 被拒),分子 = 通过
    ``独立机构`` 判定的成对。**不设线、不参与门禁**,只用于说明"同日多源原创
    覆盖"在事件层面的厚度;被拒原因不在这里解释,由调用方原样保留。
    """
    candidate = len(pairs) + len(rejected)
    return {
        "independent_pairs": len(pairs),
        "candidate_pairs": candidate,
        "value": round(len(pairs) / candidate, 4) if candidate else None,
        "meaning": "同题候选中独立机构对占比;只读观测,不参与门禁",
    }


def inventory_row(*, category: str, article_id: int, url: str, title: str | None,
                  marker: str | None, dateline: str | None, identity: Identity,
                  probed_at: str) -> dict:
    """逐篇来源身份清单的一行(落盘用,必须留下判定依据)。"""
    return {
        "category": category,
        "article_id": article_id,
        "url": url,
        "title": title,
        "page_marker": marker,
        "dateline_agency": dateline,
        "kind": identity.kind,
        "agency": identity.agency,
        "independent_eligible": identity.kind in ELIGIBLE_KINDS and identity.agency is not None,
        "is_first_hand_of_event": identity.kind == KIND_OWN,
        "evidence": identity.evidence,
        "probed_at": probed_at,
    }
