"""采集来源配置(sources.json)。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ttxt import config as cfg

SOURCES_PATH = cfg.CRAWLER_DIR / "sources.json"


@dataclass
class Source:
    id: str
    name: str
    category: str
    kind: str  # html | rss
    url: str
    enabled: bool = True
    rate_limit_s: float = 1.0
    max_pages: int = 3
    item_selector: str | None = None
    title_selector: str | None = None
    author_selector: str | None = None
    next_selector: str | None = None
    # C-7b(2026-11-15):列表卡片里"这条自己的链接"的选择器(如 ``h2 a`` / ``a.title``)。
    # 不配时抽取器按"标题元素自身/标题内链接 → 锚文本=标题 → 块内第一个可用链接"的顺序猜,
    # 并且只采信在本页唯一的那个链接;全都不成立就明确标记"没拿到逐条链接"(计数 + 记日志),
    # 绝不静默把来源页地址当文章地址(见 extract.extract_html_items / store.insert_items)。
    link_selector: str | None = None
    base_url: str | None = None
    demo: bool = False
    notes: str = ""
    extractor: str = "generic"  # generic | toutiao
    render: str = "http"  # http | browser
    max_articles: int = 10


def load_sources(path: Path | None = None) -> list[Source]:
    payload = json.loads((path or SOURCES_PATH).read_text(encoding="utf-8"))
    sources: list[Source] = []
    for raw in payload.get("sources", []):
        sources.append(Source(**raw))
    return sources


def get_source(source_id: str, path: Path | None = None) -> Source:
    for source in load_sources(path):
        if source.id == source_id:
            return source
    raise KeyError(f"source not found: {source_id}")
