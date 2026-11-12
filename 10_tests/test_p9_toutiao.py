"""P9 测试:头条适配器(列表去重/URL 规范化/正文与元数据抽取/来源配置)。"""

from scrapling.parser import Selector

from ttxt.crawler import sources, toutiao

LISTING_HTML = """<html><body>
<div class="feed-card-wrapper">
<a href="/article/1234567890/">这是一个科技新闻标题示例</a>
<a href="/article/1234567890/#comment">12评论</a>
<a href="/article/1234567890/">这是一个科技新闻标题示例</a>
<a href="https://www.toutiao.com/article/9876543210/">第二个科技新闻标题</a>
<a href="/novel/book/123">小说</a>
<a href="/article/111/#comment">5评论</a>
<a href="/article/222/">另一个有效链接标题</a>
</div></body></html>"""

ARTICLE_HTML = """<html><body><article>
<div class="article-content">
<h1>测试标题：科技新闻</h1>
<span>2026-11-12 10:01</span><span>·</span><span>测试媒体</span>
<p>第一段正文内容，关于人工智能与开源模型的发展。</p>
<p>第二段正文内容，继续说明相关技术进展。</p>
</div></article></body></html>"""


def test_canonical_article_url():
    assert toutiao.canonical_article_url("/article/123/") == "https://www.toutiao.com/article/123/"
    assert (
        toutiao.canonical_article_url("/article/123/#comment")
        == "https://www.toutiao.com/article/123/"
    )
    assert toutiao.canonical_article_url("/novel/book/123") is None
    assert toutiao.canonical_article_url("https://evil.example.com/article/123/") is None
    assert toutiao.canonical_article_url(None) is None


def test_extract_listing_dedupes_and_filters():
    page = Selector(LISTING_HTML)
    links = toutiao.extract_listing(page)
    urls = [url for url, _title in links]
    assert urls == [
        "https://www.toutiao.com/article/1234567890/",
        "https://www.toutiao.com/article/9876543210/",
        "https://www.toutiao.com/article/222/",
    ]
    assert links[0][1] == "这是一个科技新闻标题示例"


def test_extract_article_metadata_and_content():
    page = Selector(ARTICLE_HTML)
    item = toutiao.extract_article(page, "https://www.toutiao.com/article/123/")
    assert item.title == "测试标题：科技新闻"
    assert item.author == "测试媒体"
    assert item.published_at == "2026-11-12 10:01"
    assert "第一段正文内容" in item.content
    assert "第二段正文内容" in item.content
    assert "2026-11-12 10:01" not in item.content
    assert item.excerpt


def test_toutiao_source_configured():
    loaded = {source.id: source for source in sources.load_sources()}
    source = loaded["toutiao_tech"]
    assert source.extractor == "toutiao"
    assert source.render == "browser"
    assert source.category == "technology"
    assert source.url.startswith("https://www.toutiao.com/")
