"""条目抽取:HTML 选择器与 RSS/Atom 解析。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from . import clean
from .clean import excerpt, normalize_text
from .sources import Source

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# 这些 href 前缀不是"这一条自己的文章地址"(页内锚点/脚本/联系方式),不得当条目 URL
NON_ARTICLE_HREF = ("#", "javascript:", "mailto:", "tel:", "data:", "blob:")


@dataclass
class ExtractedItem:
    title: str
    content: str
    excerpt: str
    author: str | None
    published_at: str | None
    url: str
    source_item_id: str | None = None
    # C-7b(2026-11-15):True = 这一条没拿到自己的链接,URL 退回列表页/来源页地址。
    # 这种 URL 不是文章身份;入库侧必须计数 + 记日志,不得静默(见 store.insert_items)。
    url_is_page_fallback: bool = False


def _element_text(element: object) -> str:
    text = getattr(element, "text", "")
    return normalize_text(str(text or ""))


def _link_hrefs(element: object, selector: str | None = None) -> list[str]:
    """元素内 ``<a href>`` 的原始取值,按文档顺序;``selector`` 为空 = 元素内全部 ``<a>``。

    Scrapling 0.5.1 实测(探针 ``07_tmp/p18_c7b_probe/probe_css.py``):没有 ``css_first``;
    ``css("a::attr(href)")`` 返回带 ``.text`` 的文本对象而不是 ``str``,这里统一收成 ``str``。
    """
    target = selector if selector and "::attr" in selector else f"{selector or 'a'}::attr(href)"
    values: list[str] = []
    for found in element.css(target):
        raw = str(getattr(found, "text", found) or "").strip()
        if raw:
            values.append(raw)
    return values


def _own_href(element: object) -> list[str]:
    """元素自己就是 ``<a>`` 时的 href(列表标题常被整条包进链接里)。"""
    if str(getattr(element, "tag", "") or "").lower() != "a":
        return []
    attrib = getattr(element, "attrib", None) or {}
    raw = str(attrib.get("href") or "").strip()
    return [raw] if raw else []


def _anchor_pairs(element: object) -> list[tuple[str, str]]:
    """``(href, 锚文本)`` 成对返回:"锚文本就是标题"是列表页里最稳的逐条链接判据。"""
    pairs: list[tuple[str, str]] = []
    for anchor in element.css("a"):
        attrib = getattr(anchor, "attrib", None) or {}
        href = str(attrib.get("href") or "").strip()
        if href:
            pairs.append((href, _element_text(anchor)))
    return pairs


def _title_match(text: str, title: str) -> bool:
    """锚文本与标题是否可信地指同一件事:相等,或互相包含且**短的那条不少于 20 字符**。

    纯"互相包含"太松:实测取证(``07_tmp/p18_c7b_probe/probe_live_demo.py``,2026-11-15 抓
    quotes.toscrape 真实列表页)里标签链接的锚文本 ``change`` 恰好出现在卡片文本里,宽松匹配
    会把 ``/tag/change/page/1/`` 当成文章地址——比塌陷更隐蔽的假身份。
    """
    if not text or not title:
        return False
    if text == title:
        return True
    shorter, longer = (text, title) if len(text) <= len(title) else (title, text)
    return len(shorter) >= 20 and shorter in longer


def _candidate_links(block: object, source: Source, title: str, base: str) -> list[str]:
    """这一条的候选链接,按可信度从高到低(已绝对化 + 规范化,已去掉来源页自身)。

    顺序与理由(实测列表卡片里往往先出现作者页/标签页链接,它们**不是**文章地址):

    1. ``source.link_selector`` —— 来源配置里点名的逐条链接,最可信;
    2. 标题元素自身就是 ``<a>``(整条标题被包进链接);
    3. 标题元素内部的 ``<a>``;
    4. 锚文本与标题**可信相等**的链接(见 ``_title_match``);
    5. 以上都不成立 → 不猜(见函数体注释:实测会把标签页当成文章地址)。
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def add(values: list[str]) -> None:
        for href in values:
            if href.lower().startswith(NON_ARTICLE_HREF):
                continue
            try:
                value = clean.normalize_url(href, base=base)
            except Exception:  # noqa: BLE001 - 单条链接解析失败不得毁掉整页抽取
                continue
            if not value or value == base or value in seen:
                continue
            seen.add(value)
            ordered.append(value)

    # 只有来源点名了 link_selector 才走这一路;selector 为空时 _link_hrefs 返回块内全部
    # 链接,那样会把作者页/标签页链接排到最前面(实测探针
    # ``07_tmp/p18_c7b_probe/probe_candidates.py``),兜底路必须在最后
    if source.link_selector:
        add(_link_hrefs(block, source.link_selector))
    if source.title_selector:
        found = block.css(source.title_selector)
        if len(found):
            add(_own_href(found[0]))
            add(_link_hrefs(found[0], None))
    add(
        [
            href
            for href, text in _anchor_pairs(block)
            if _title_match(text, title)
        ]
    )
    # 5. 没有更可信的证据就**不猜**。实测取证(``07_tmp/p18_c7b_probe/probe_live_demo.py``,
    #    2026-11-14 抓 quotes.toscrape 真实列表页):卡片里的链接只有标签页和(有时)作者页,
    #    "块内只有一个链接"恰好是标签页 ``/tag/change/page/1/`` —— 采信它就等于给文章编一个
    #    假地址,比塌陷更隐蔽。所以这里明确回退,由入库侧计数 + doctor 天天喊,
    #    整改办法是来源里配 ``link_selector``(或直接改用带逐条链接的列表页)。
    return ordered


def extract_html_items(page: object, source: Source, page_url: str | None = None) -> list[ExtractedItem]:
    """列表页抽取:每一条必须取**自己**的链接,拿不到就明确标记回退,绝不静默塌陷。

    C-7b(2026-11-15)修的就是本函数的老第 55 行 ``url=source.url``:实测 ``demo`` 库
    30 条互不相同的名言共用同一个 ``https://quotes.toscrape.com/``,URL 文章身份彻底
    塌陷,而入库侧只把它当"正常重复"计数,三天后才被 C-7 判重普查发现
    (``08_wiki/reports/P17-session-handoff-20261115.md`` §五 待办 2)。现在:

    - 逐条按 ``_candidate_links`` 的可信度顺序取链接,基准用**当前这一页**的地址
      (``page_url``,分页后是第 2/3 页自己的 URL),不是 ``source.url``,否则相对链接会错页;
    - **页内唯一性**:两条抽到同一个链接,说明那个链接不是"每条自己的"文章地址
      (作者页/标签页链接就是这种),一律不采信,退回标记 ``url_is_page_fallback=True``;
      塌陷就此变成"看得见、可计数"的回退,而不是静默的错误身份;
    - 历史遗留的塌陷行**不得删**(规则 33),由 ``doctor.db.url_identity`` 天天对账。

    正文抽取本批不做(``content=title_raw`` 保持原样),那是另一件事,不混进身份整改。
    """
    if not source.item_selector:
        raise ValueError(f"source {source.id} missing item_selector")
    base = clean.normalize_url(page_url or source.url)
    blocks = list(page.css(source.item_selector))
    metas: list[tuple[str, str, str | None]] = []
    candidates: list[list[str]] = []
    for block in blocks:
        title_raw = ""
        if source.title_selector:
            found = block.css(source.title_selector)
            title_raw = _element_text(found[0]) if len(found) else ""
        if not title_raw:
            title_raw = _element_text(block)
        author = None
        if source.author_selector:
            found = block.css(source.author_selector)
            author = _element_text(found[0]) if len(found) else None
        title = title_raw[:120]
        metas.append((title_raw, title, author))
        candidates.append(_candidate_links(block, source, title, base))

    # 每个候选链接在本页被几条命中;>1 就不是逐条链接(见 docstring)
    spread = Counter(value for links in candidates for value in set(links))
    items: list[ExtractedItem] = []
    for (title_raw, title, author), links in zip(metas, candidates):
        chosen = next((value for value in links if spread[value] == 1), "")
        items.append(
            ExtractedItem(
                title=title,
                content=title_raw,
                excerpt=excerpt(title_raw),
                author=author,
                published_at=None,
                url=chosen or base,
                url_is_page_fallback=not bool(chosen),
            )
        )
    return items


def _rss_datetime(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    try:
        return parsedate_to_datetime(raw).astimezone().isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001 - 非 RFC822 时间按原样保留
        return raw


def _absolutize(link: str, base: str) -> str:
    """只在链接是相对写法时绝对化;绝对 URL 原样保留(不剥 utm 等参数,入库口径不变)。"""
    if not link or "://" in link or link.lower().startswith(NON_ARTICLE_HREF):
        return link
    return clean.normalize_url(link, base=base)


def extract_rss_items(xml_text: str, source: Source) -> list[ExtractedItem]:
    root = ET.fromstring(xml_text)
    items: list[ExtractedItem] = []

    # RSS 2.0
    for node in root.findall(".//item"):
        title = normalize_text(node.findtext("title", ""))
        link = _absolutize((node.findtext("link", "") or "").strip(), source.url)
        description = normalize_text(node.findtext("description", "")) or normalize_text(node.findtext("summary", ""))
        author = normalize_text(node.findtext("author", "")) or None
        published = _rss_datetime(node.findtext("pubDate"))
        if not title:
            continue
        items.append(
            ExtractedItem(
                title=title,
                content=description or title,
                excerpt=excerpt(description or title),
                author=author,
                published_at=published,
                # C-7b:缺 link 时回退订阅地址只是"没有身份",必须标记出来交给入库侧计数 + 记日志
                url=link or source.url,
                url_is_page_fallback=not bool(link),
            )
        )
    if items:
        return items

    # Atom
    for node in root.findall(".//atom:entry", ATOM_NS):
        title = normalize_text(node.findtext("atom:title", "", namespaces=ATOM_NS))
        summary = normalize_text(node.findtext("atom:summary", "", namespaces=ATOM_NS)) or normalize_text(
            node.findtext("atom:content", "", namespaces=ATOM_NS)
        )
        author = normalize_text(node.findtext("atom:author/atom:name", "", namespaces=ATOM_NS)) or None
        published = _rss_datetime(node.findtext("atom:updated", "", namespaces=ATOM_NS)) or _rss_datetime(
            node.findtext("atom:published", "", namespaces=ATOM_NS)
        )
        link_node = node.find("atom:link", ATOM_NS)
        link = (link_node.get("href") if link_node is not None else "") or ""
        # Atom 的 link 允许是相对地址,按订阅地址绝对化,否则同一篇文章会因写法不同被认成两篇
        link = _absolutize(link.strip(), source.url)
        if not title:
            continue
        items.append(
            ExtractedItem(
                title=title,
                content=summary or title,
                excerpt=excerpt(summary or title),
                author=author,
                published_at=published,
                url=link or source.url,
                url_is_page_fallback=not bool(link),
            )
        )
    return items
