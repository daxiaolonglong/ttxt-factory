# -*- coding: utf-8 -*-
"""R37:文章页发布时间取证(HTML 列表源补日期)的边界测试。

口径:只认可复核证据(meta / JSON-LD / <time> / 全页唯一日期文本);
同页出现多个日期时返回 None —— 宁可留空,不给文章编日期。
"""

from __future__ import annotations

from ttxt.crawler import extract


class _Page:
    def __init__(self, html: str) -> None:
        self.html_content = html


class _Element:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_all_text(self) -> str:
        return self._text


class _PageWithCss(_Page):
    def __init__(self, html: str, found: dict) -> None:
        super().__init__(html)
        self._found = found

    def css(self, selector: str):
        return self._found.get(selector, [])


def test_meta_published_time():
    html = '<meta property="article:published_time" content="2026-11-18T21:18:00+08:00">'
    assert extract.extract_published_at(_Page(html)) == "2026-11-18T21:18:00+08:00"


def test_json_ld_date_published():
    html = '<script type="application/ld+json">{"datePublished":"2026-11-18 21:18:00"}</script>'
    assert extract.extract_published_at(_Page(html)).startswith("2026-11-18T21:18:00")


def test_time_element():
    html = '<time datetime="2026-11-18T21:18:00+08:00">昨天</time>'
    assert extract.extract_published_at(_Page(html)) == "2026-11-18T21:18:00+08:00"


def test_unique_inline_datetime():
    html = '<h1>标题</h1><em class="f-fl">2026-11-16 11:18:35</em><p>正文</p>'
    assert extract.extract_published_at(_Page(html)).startswith("2026-11-16T11:18:35")


def test_ambiguous_page_returns_none():
    html = "<p>2026-11-16 11:18:35</p><p>2026-11-17 09:00:00</p>"
    assert extract.extract_published_at(_Page(html)) is None


def test_same_instant_two_spellings_is_unique():
    """NBD 实测:同一时刻以 ``T`` 与空格两种写法各出现一次,仍算唯一。"""
    html = '<p>2026-11-19T00:28:39</p><p>2026-11-19 00:28:39</p>'
    assert extract.extract_published_at(_Page(html)).startswith("2026-11-19T00:28:39")


def test_selector_wins_over_ambiguous_page():
    """页面另有别的日期(页脚「下一篇文章 20:00」)时,按来源声明的容器取证。"""
    from types import SimpleNamespace

    html = "<p>2026-11-18 20:00</p><p>2026-11-18 19:57:15</p>"
    page = _PageWithCss(html, {"p.u-time span.time": [_Element("2026-11-18 19:57:15")]})
    source = SimpleNamespace(item_date_selectors=["p.u-time span.time"])
    assert extract.extract_published_at(page, source).startswith("2026-11-18T19:57:15")
    # 没声明选择器时仍按唯一性规则判,多日期一律 None
    assert extract.extract_published_at(page) is None


def test_no_date_returns_none():
    assert extract.extract_published_at(_Page("<p>没有日期</p>")) is None
    assert extract.extract_published_at(_Page("")) is None


def test_unparseable_date_is_not_guessed():
    assert extract.extract_published_at(_Page("<p>2026-13-40 99:99:99</p>")) is None
