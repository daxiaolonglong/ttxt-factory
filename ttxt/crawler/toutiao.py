"""今日头条适配器:科技频道列表(浏览器渲染)→ 文章页正文抽取。

合规边界:robots.txt 未禁止频道页与 /article/ 路径;使用本机 Edge 正常渲染,
不登录、不绕过验证码/访问控制;逐篇限速;逐 URL 检查 robots。
"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlsplit

from ttxt import config as cfg

from . import cleaner, fetcher, robots
from .clean import excerpt
from .extract import ExtractedItem
from .sources import Source

BASE = "https://www.toutiao.com"
ARTICLE_RE = re.compile(r"^/article/(\d+)/?$")
META_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*·\s*(\S+)")


def canonical_article_url(href: str | None) -> str | None:
    if not href:
        return None
    absolute = urljoin(BASE, href)
    parts = urlsplit(absolute)
    if parts.hostname not in ("www.toutiao.com", "m.toutiao.com"):
        return None
    match = ARTICLE_RE.match(parts.path)
    if not match:
        return None
    return f"{BASE}/article/{match.group(1)}/"


def extract_listing(page) -> list[tuple[str, str]]:
    """从频道页提取去重后的 (文章URL, 标题);过滤评论锚点与短标题。"""
    seen: dict[str, str] = {}
    for anchor in page.css("a[href*='/article/']"):
        href = anchor.attrib.get("href") or ""
        title = (anchor.text or "").strip()
        if "comment" in href:
            continue
        url = canonical_article_url(href)
        if not url or url in seen:
            continue
        if len(title) < 6 or title.endswith("评论"):
            continue
        seen[url] = title
    return list(seen.items())


def extract_article(page, url: str) -> ExtractedItem:
    title = ""
    heading = page.css("h1::text").get()
    if heading and heading.strip():
        title = heading.strip()
    if not title:
        raw_title = page.css("title::text").get() or ""
        title = raw_title.rsplit(" - ", 1)[0].strip()

    texts: list[str] = []
    for selector in (".article-content", "article"):
        elements = page.css(selector)
        if elements:
            texts = [text.strip() for text in elements[0].css("*::text").getall() if text and text.strip()]
            if len(" ".join(texts)) >= 200:
                break
    joined = "\n".join(texts)
    if len(joined) < 200:
        joined = " ".join(
            text.strip() for text in page.css("body ::text").getall() if text and text.strip()
        )

    meta = META_RE.search(joined)
    published_at = meta.group(1) if meta else None
    author = meta.group(2) if meta else None

    content = joined
    if meta:
        content = content.replace(meta.group(0), "\n")
    if title:
        content = content.replace(title, "\n", 1)
    content = content.replace("·", " ")
    content = cleaner.clean_article(content) or joined
    return ExtractedItem(
        title=title or "（无标题）",
        content=content,
        excerpt=excerpt(content),
        author=author,
        published_at=published_at,
        url=url,
    )


def collect(
    source: Source,
    limit: int | None = None,
    rules: robots.RobotsRules | None = None,
    session=None,
) -> dict:
    """抓取头条列表页 + 文章页。

    P12:``rules`` 传入 runner 已取好的 host 级规则(不再自行重取 robots);
    ``session`` 传入调用方持有的浏览器会话 —— 整次采集共用一个浏览器,不再逐 URL 起停。
    """
    user_agent = str(cfg.load_config().get("crawler", {}).get("user_agent", "TouTiaoSmartFactory/0.1"))
    if rules is None:
        rules = robots.fetch_robots(source.url, user_agent)
    if rules.unknown:
        raise RuntimeError("robots.txt 无法确认,来源暂停,不放行")
    listing_path = robots.target_path(source.url)
    if not rules.allowed(listing_path):
        raise RuntimeError(f"robots.txt disallows listing path: {listing_path}")

    delay = max(float(source.rate_limit_s or 2.0), float(rules.crawl_delay_s or 0))
    listing = fetcher.fetch_page(source.url, render=True, session=session)
    pages = 1  # 真实抓页数:观测字段必须与实际抓取次数一致,不虚报 0
    links = extract_listing(listing.page)
    max_articles = max(1, int(limit or source.max_articles or 10))

    items: list[ExtractedItem] = []
    robots_skipped = 0
    errors = 0
    for url, _title in links[:max_articles]:
        path = robots.target_path(url)
        if not rules.allowed(path):
            robots_skipped += 1
            continue
        try:
            page = fetcher.fetch_page(url, render=True, session=session)
            pages += 1
            items.append(extract_article(page.page, url))
        except Exception:  # noqa: BLE001 - 单篇失败不中断整批
            errors += 1
            continue
        time.sleep(delay)
    return {
        "items": items,
        "links": len(links),
        "pages": pages,
        "robots_skipped": robots_skipped,
        "errors": errors,
    }
