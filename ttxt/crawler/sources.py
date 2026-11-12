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
