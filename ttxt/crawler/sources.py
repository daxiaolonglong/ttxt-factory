"""采集来源配置(sources.json)。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    # P19(2026-11-15)关键词驱动采集(用户目标 1/2:关键词决定采什么)。
    # 根因:关键词层只"反哺入队",入队的是同一个固定来源任务,关键词本身
    # 从来不进入采集动作(见 auto_feedback:154 取 candidates_sources[0])。
    # keywords:来源自带关键词;为空时由 keyword_crawl 取类目库里 gap/高分词。
    keywords: list[str] = field(default_factory=list)
    # url_template:含 ``{keyword}`` 的检索页模板。**只对 robots.txt 放行的地址生效**;
    # 实测(probe1/probe2)百度/360/搜狗/头条搜索/google news 全部 disallow,
    # 因此这些站点不得配在这里——不是能力问题,是访问控制问题(规则:永不绕过)。
    url_template: str | None = None
    # fetch_item_pages:逐条抓文章页补正文。RSS/列表页只给摘要时必须开
    # (实测 probe2/probe3:中新网 RSS 摘要中位数 71–108 字符,澎湃列表只有标题)。
    fetch_item_pages: bool = False
    # max_item_pages:单次任务最多补几条正文,防止一次任务无限扩张(规则 25 磁盘/时间有界)。
    max_item_pages: int = 10
    # item_content_selectors:文章页正文选择器(按顺序试,取到足够长就停);
    # 为空时用 extract.BODY_SELECTORS 通用猜测,猜不到就保留原摘要,不猜不编造。
    item_content_selectors: list[str] = field(default_factory=list)
    # item_date_selectors:文章页里"本条发布时间"容器的选择器(按顺序试,取第一条能解析的)。
    # 为空时只用 meta / JSON-LD / <time> / 全页唯一时间四类通用证据;站点把时间放在
    # 固定容器、而页面别处还有别的日期(如"下一篇文章 20:00")时,靠它取证,不猜日期。
    item_date_selectors: list[str] = field(default_factory=list)


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
