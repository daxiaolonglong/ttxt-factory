"""P2 测试:URL 规范化、哈希、robots、抽取、RSS/Atom、去重入库、来源配置。"""

from ttxt.crawler import clean, extract, robots, sources, store

HTML = """<html><body>
<div class="quote"><span class="text">“Stay hungry”</span><span>by <small class="author">Steve Jobs</small></span></div>
<div class="quote"><span class="text">“Simplicity”</span><span>by <small class="author">Leonardo da Vinci</small></span></div>
</body></html>"""

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Article A</title><link>https://example.com/a</link><description>Summary A</description><pubDate>Tue, 11 Nov 2025 08:00:00 +0800</pubDate></item>
<item><title>Article B</title><link>https://example.com/b</link><description>Summary B</description><pubDate>Wed, 12 Nov 2025 09:30:00 +0800</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom A</title><link href="https://example.com/atom-a"/><updated>2025-11-12T08:00:00+08:00</updated><summary>Atom summary</summary><author><name>Alice</name></author></entry>
</feed>"""


def test_normalize_url():
    result = clean.normalize_url("HTTPS://Example.COM:443/a/?utm_source=x&b=2&a=1#frag")
    assert result == "https://example.com/a/?a=1&b=2"


def test_content_hash_stable_and_whitespace_insensitive():
    assert clean.content_hash("  Title ", "body\n") == clean.content_hash("Title", " body ")


def test_robots_longest_match():
    text = "User-agent: *\nDisallow: /private\nAllow: /private/public\nCrawl-delay: 2\n"
    rules = robots.parse_robots(text)
    assert not rules.allowed("/private/x")
    assert rules.allowed("/private/public/x")
    assert rules.allowed("/other")
    assert rules.crawl_delay_s == 2.0


def test_robots_missing_allows_all():
    rules = robots.parse_robots("")
    assert rules.allowed("/anything")


def test_extract_html_items():
    from scrapling.parser import Selector

    page = Selector(HTML)
    source = sources.Source(
        id="fixture",
        name="fixture",
        category="technology",
        kind="html",
        url="https://example.com/",
        item_selector=".quote",
        title_selector=".text",
        author_selector=".author",
    )
    items = extract.extract_html_items(page, source)
    assert len(items) == 2
    assert items[0].title == "“Stay hungry”"
    assert items[0].author == "Steve Jobs"


def test_extract_rss_items():
    source = sources.Source(id="rss", name="rss", category="technology", kind="rss", url="https://example.com/feed")
    items = extract.extract_rss_items(RSS, source)
    assert [item.title for item in items] == ["Article A", "Article B"]
    assert items[0].url == "https://example.com/a"
    assert items[0].published_at is not None


def test_extract_atom_items():
    source = sources.Source(id="atom", name="atom", category="technology", kind="rss", url="https://example.com/atom")
    items = extract.extract_rss_items(ATOM, source)
    assert len(items) == 1
    assert items[0].title == "Atom A"
    assert items[0].author == "Alice"
    assert items[0].url == "https://example.com/atom-a"


def test_store_dedupe(tmp_path):
    path = store.init_articles_db("_demo", db_path=tmp_path / "articles.db")
    item = extract.ExtractedItem(
        title="T1",
        content="C1",
        excerpt="e",
        author="A",
        published_at=None,
        url="https://example.com/1",
    )
    first = store.insert_items("_demo", "src", [item], db_path=path)
    assert first == {"inserted": 1, "duplicates": 0}
    second = store.insert_items("_demo", "src", [item], db_path=path)
    assert second == {"inserted": 0, "duplicates": 1}
    assert store.count_articles("_demo", db_path=path) == 1


def test_sources_config_valid():
    loaded = sources.load_sources()
    ids = {source.id for source in loaded}
    assert {"quotes_toscrape", "sspai_rss", "ruanyifeng_atom"} <= ids
    for source in loaded:
        assert source.url.startswith("http")
        assert source.kind in {"html", "rss"}
