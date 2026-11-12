"""条目抽取:HTML 选择器与 RSS/Atom 解析。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from .clean import excerpt, normalize_text
from .sources import Source

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class ExtractedItem:
    title: str
    content: str
    excerpt: str
    author: str | None
    published_at: str | None
    url: str
    source_item_id: str | None = None


def _element_text(element: object) -> str:
    text = getattr(element, "text", "")
    return normalize_text(str(text or ""))


def extract_html_items(page: object, source: Source) -> list[ExtractedItem]:
    if not source.item_selector:
        raise ValueError(f"source {source.id} missing item_selector")
    blocks = page.css(source.item_selector)
    items: list[ExtractedItem] = []
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
        items.append(
            ExtractedItem(
                title=title,
                content=title_raw,
                excerpt=excerpt(title_raw),
                author=author,
                published_at=None,
                url=source.url,
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


def extract_rss_items(xml_text: str, source: Source) -> list[ExtractedItem]:
    root = ET.fromstring(xml_text)
    items: list[ExtractedItem] = []

    # RSS 2.0
    for node in root.findall(".//item"):
        title = normalize_text(node.findtext("title", ""))
        link = (node.findtext("link", "") or "").strip()
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
                url=link or source.url,
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
        link = link_node.get("href") if link_node is not None else ""
        if not title:
            continue
        items.append(
            ExtractedItem(
                title=title,
                content=summary or title,
                excerpt=excerpt(summary or title),
                author=author,
                published_at=published,
                url=(link or source.url).strip(),
            )
        )
    return items
