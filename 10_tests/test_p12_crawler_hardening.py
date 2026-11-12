"""P12 第一批(采集稳定性)验收测试。

覆盖缺陷 ①逐 URL 起浏览器 ③render/session 未贯通 ⑤幽灵任务无限重排 ⑥housekeep 统计漏桶,
以及新增的:分页逐 URL robots 复检、跨 host 拦截、分页环检测、host 级规则缓存、
状态码终态/重试策略、aborted 终态语义。

约定:全部使用 tmp 路径,绝不触碰 01_categories 生产库与真实网络;
浏览器一律以 _open_session/_close_session 替身模拟,不启动真实 Edge。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from ttxt import config as cfg
from ttxt import db, housekeep
from ttxt.crawler import fetcher, robots, runner, store
from ttxt.crawler import sources as source_loader
from ttxt.crawler import toutiao


# ---------------------------------------------------------------- 替身基础设施

class FakeRules:
    """duck-typing robots.RobotsRules:只暴露 gate/runner 实际使用的成员。"""

    def __init__(self, disallow: tuple[str, ...] = (), unknown: bool = False):
        self.disallow = disallow
        self.unknown = unknown
        self.crawl_delay_s = None

    def allowed(self, path: str) -> bool:
        # 与真实 RobotsRules 一致:unknown 一律不放行
        if self.unknown:
            return False
        return not any(path.startswith(p) for p in self.disallow)


class FakeCss:
    def __init__(self, items):
        self._items = list(items)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, i):
        return self._items[i]

    def get(self):
        return self._items[0] if self._items else None

    def getall(self):
        return list(self._items)


class FakeElement:
    def __init__(self, text="", attrib=None):
        self.text = text
        self.attrib = attrib or {}


class FakePage:
    """只实现 extract/toutiao 实际用到的 css() 选择器。"""

    def __init__(self, mapping: dict[str, object]):
        self._mapping = mapping

    def css(self, selector: str):
        return FakeCss(self._mapping.get(selector, []))


class FakeSession:
    def __init__(self):
        self.fetched: list[str] = []

    def fetch(self, url: str):
        self.fetched.append(url)
        return SimpleNamespace(status=200, text="")


@pytest.fixture
def system_db(tmp_path, monkeypatch):
    path = tmp_path / "system.db"
    db.init_system_db(path)
    monkeypatch.setattr(store, "system_db_path", lambda: path)
    return path


def job_status(db_path: Path, job_id: int) -> sqlite3.Row:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        con.close()


def write_sources(tmp_path: Path, monkeypatch, entries: list[dict]) -> Path:
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps({"sources": entries}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(source_loader, "SOURCES_PATH", path)
    return path


def html_source_entry(**overrides) -> dict:
    entry = {
        "id": "fake_html",
        "name": "fake html",
        "kind": "html",
        "category": "demo",
        "url": "https://demo.example.com/page1/",
        "enabled": True,
        "render": "http",
        "rate_limit_s": 0.0,
        "max_pages": 5,
        "item_selector": ".quote",
        "title_selector": None,
        "author_selector": None,
        "max_articles": 20,
        "demo": True,
    }
    entry.update(overrides)
    return entry


class FetchRecorder:
    def __init__(self, pages: dict[str, FakePage]):
        self.pages = pages
        self.calls: list[dict] = []

    def __call__(self, url, timeout=None, render=False, session=None):
        self.calls.append({"url": url, "render": render, "session": session})
        page = self.pages[url]
        return SimpleNamespace(page=page, status=200, url=url, elapsed_s=0.0)


def quote_page(next_href: str | None = None, quotes=("第一块引文内容甲", "第二块引文内容乙")) -> FakePage:
    mapping: dict[str, object] = {".quote": [FakeElement(text=q) for q in quotes]}
    if next_href is not None:
        mapping[".next a"] = [FakeElement(attrib={"href": next_href})]
    return FakePage(mapping)


@pytest.fixture
def fake_insert(monkeypatch):
    captured: list[tuple] = []

    def _fake(category, source_id, items, *args, **kwargs):
        captured.append((category, source_id, list(items)))
        return {"inserted": len(items), "duplicates": 0}

    monkeypatch.setattr(store, "insert_items", _fake)
    return captured


# ---------------------------------------------------------------- robots.gate

def test_robots_gate_caches_rules_per_host(monkeypatch):
    calls: list[str] = []

    def fake_fetch_rules(url, ua, timeout=10.0):
        calls.append(url)
        return FakeRules()

    monkeypatch.setattr(robots, "fetch_rules", fake_fetch_rules)
    cache: dict = {}
    rules1, ok1 = robots.gate("https://a.example.com/x/", "ua", cache)
    rules2, ok2 = robots.gate("https://a.example.com/y/", "ua", cache)
    rules3, ok3 = robots.gate("https://b.example.com/", "ua", cache)
    assert ok1 and ok2 and ok3
    assert rules1 is rules2  # host 级缓存:同 host 复用同一规则对象
    assert calls == ["https://a.example.com/x/", "https://b.example.com/"]  # 同 host 不重复取


def test_robots_gate_unknown_never_allows(monkeypatch):
    monkeypatch.setattr(
        robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules(unknown=True)
    )
    cache: dict = {}
    rules, ok = robots.gate("https://a.example.com/", "ua", cache)
    assert rules.unknown is True
    assert ok is False
    # unknown 同样被缓存,不会每次重试都打远端
    rules2, ok2 = robots.gate("https://a.example.com/other", "ua", cache)
    assert ok2 is False


# ---------------------------------------------------------------- 分页门禁

def test_pagination_disallowed_page_stops_before_fetch(tmp_path, monkeypatch, system_db, fake_insert):
    write_sources(tmp_path, monkeypatch, [html_source_entry(next_selector=".next a")])
    store.create_crawl_job("fake_html", "demo", db_path=system_db)
    pages = {
        "https://demo.example.com/page1/": quote_page(next_href="/page2/"),
    }
    recorder = FetchRecorder(pages)
    monkeypatch.setattr(fetcher, "fetch_page", recorder)
    rule_sets = {"demo.example.com": FakeRules(disallow=("/page2",))}
    gate_calls: list[str] = []

    def fake_fetch_rules(url, ua, timeout=10.0):
        gate_calls.append(url)
        from urllib.parse import urlsplit

        return rule_sets[urlsplit(url).netloc]

    monkeypatch.setattr(robots, "fetch_rules", fake_fetch_rules)

    results = runner.run_queued(limit=5)
    assert results and results[0]["ok"] is True
    summary = results[0]
    assert recorder.calls and [c["url"] for c in recorder.calls] == ["https://demo.example.com/page1/"]
    assert summary["pages_fetched"] == 1
    assert summary["robots_skipped"] == 1
    assert summary["pagination_stop"] == "robots"
    # 整次任务对该 host 只取一次 robots.txt(列表首检 + 分页复检共用缓存)
    assert gate_calls == ["https://demo.example.com/page1/"]


def test_pagination_cross_host_stops(tmp_path, monkeypatch, system_db, fake_insert):
    write_sources(tmp_path, monkeypatch, [html_source_entry(next_selector=".next a")])
    store.create_crawl_job("fake_html", "demo", db_path=system_db)
    pages = {
        "https://demo.example.com/page1/": quote_page(next_href="https://evil.example.com/page2/"),
    }
    recorder = FetchRecorder(pages)
    monkeypatch.setattr(fetcher, "fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())

    results = runner.run_queued(limit=5)
    assert results and results[0]["ok"] is True
    summary = results[0]
    assert [c["url"] for c in recorder.calls] == ["https://demo.example.com/page1/"]
    assert summary["pagination_stop"] == "cross_host"
    assert summary["pages_fetched"] == 1


def test_pagination_loop_guard(tmp_path, monkeypatch, system_db, fake_insert):
    write_sources(tmp_path, monkeypatch, [html_source_entry(next_selector=".next a")])
    store.create_crawl_job("fake_html", "demo", db_path=system_db)
    pages = {
        "https://demo.example.com/page1/": quote_page(next_href="/page2/"),
        "https://demo.example.com/page2/": quote_page(next_href="/page1/", quotes=("第三块引文丙丁",)),
    }
    recorder = FetchRecorder(pages)
    monkeypatch.setattr(fetcher, "fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())

    results = runner.run_queued(limit=5)
    assert results and results[0]["ok"] is True
    summary = results[0]
    urls = [c["url"] for c in recorder.calls]
    assert urls == ["https://demo.example.com/page1/", "https://demo.example.com/page2/"]
    assert summary["pagination_stop"] == "loop"
    assert summary["pages_fetched"] == 2


# ---------------------------------------------------------------- 浏览器会话贯通

@pytest.fixture
def fake_browser(monkeypatch):
    state = {"open": 0, "close": 0, "session": FakeSession()}

    def _open():
        state["open"] += 1
        return state["session"]

    def _close(session):
        assert session is state["session"]
        state["close"] += 1

    monkeypatch.setattr(fetcher, "_open_session", _open)
    monkeypatch.setattr(fetcher, "_close_session", _close)
    return state


def test_render_browser_opens_one_shared_session(tmp_path, monkeypatch, system_db, fake_insert, fake_browser):
    write_sources(tmp_path, monkeypatch, [html_source_entry(render="browser", max_pages=1)])
    store.create_crawl_job("fake_html", "demo", db_path=system_db)
    recorder = FetchRecorder({"https://demo.example.com/page1/": quote_page()})
    monkeypatch.setattr(fetcher, "fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())

    results = runner.run_queued(limit=5)
    assert results and results[0]["ok"] is True
    assert recorder.calls[0]["render"] is True
    assert recorder.calls[0]["session"] is fake_browser["session"]
    assert fake_browser["open"] == 1
    assert fake_browser["close"] == 1  # 任务结束会话即关闭,不泄漏


def test_http_source_never_opens_browser(tmp_path, monkeypatch, system_db, fake_insert, fake_browser):
    write_sources(tmp_path, monkeypatch, [html_source_entry(render="http", max_pages=1)])
    store.create_crawl_job("fake_html", "demo", db_path=system_db)
    recorder = FetchRecorder({"https://demo.example.com/page1/": quote_page()})
    monkeypatch.setattr(fetcher, "fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())

    results = runner.run_queued(limit=5)
    assert results and results[0]["ok"] is True
    assert recorder.calls[0]["render"] is False
    assert recorder.calls[0]["session"] is None
    assert fake_browser["open"] == 0


def test_browser_session_nesting_shares_single_session(fake_browser):
    with fetcher.browser_session() as outer:
        with fetcher.browser_session() as inner:
            assert outer is inner
        assert fake_browser["open"] == 1
        assert fake_browser["close"] == 0  # 内层退出不关闭
    assert fake_browser["close"] == 1


def test_session_context_reuses_given_session(fake_browser):
    existing = FakeSession()
    with fetcher.session_context(existing) as active:
        assert active is existing
    assert fake_browser["open"] == 0

    result = fetcher._browser_get("https://x.example.com/", existing)
    assert existing.fetched == ["https://x.example.com/"]
    assert fake_browser["open"] == 0

    fetcher._browser_get("https://y.example.com/", None)
    assert fake_browser["open"] == 1
    assert fake_browser["close"] == 1  # 无会话时临时开会话,用完即关


# ---------------------------------------------------------------- 状态码策略

def test_terminal_status_no_retry(monkeypatch):
    calls: list[int] = []
    sleeps: list[float] = []

    def fake_fetch_once(url, timeout, render, session):
        calls.append(1)
        return SimpleNamespace(status=403)

    monkeypatch.setattr(fetcher, "_fetch_once", fake_fetch_once)
    monkeypatch.setattr(fetcher.time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(fetcher.FetchError) as exc:
        fetcher.fetch_page("https://x.example.com/")
    assert exc.value.terminal is True
    assert exc.value.status == 403
    assert len(calls) == 1  # 终态绝不重试
    assert sleeps == []


def test_retryable_status_backoff_then_success(monkeypatch):
    statuses = iter([503, 429, 200])
    calls: list[int] = []
    sleeps: list[float] = []

    def fake_fetch_once(url, timeout, render, session):
        calls.append(1)
        return SimpleNamespace(status=next(statuses))

    monkeypatch.setattr(fetcher, "_fetch_once", fake_fetch_once)
    monkeypatch.setattr(fetcher.time, "sleep", lambda s: sleeps.append(s))
    result = fetcher.fetch_page("https://x.example.com/")
    assert result.status == 200
    assert len(calls) == 3
    assert sleeps == [2, 20]  # 5xx 指数退避;429 按 10*attempt 加倍退避


# ---------------------------------------------------------------- toutiao 会话/规则透传

def test_toutiao_collect_reuses_rules_and_session(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("collect 不应自行重取 robots,必须复用调用方传入的 rules")

    monkeypatch.setattr(robots, "fetch_robots", boom)
    monkeypatch.setattr(toutiao.time, "sleep", lambda s: None)

    listing = FakePage({
        "a[href*='/article/']": [
            FakeElement(attrib={"href": "/article/111/"}, text="人工智能制造业报道标题"),
            FakeElement(attrib={"href": "/article/comment/222/"}, text="这条带评论锚点"),
            FakeElement(attrib={"href": "/article/333/"}, text="被robots禁止的文章标题"),
        ]
    })
    article = FakePage({
        "h1::text": [],
        "title::text": ["人工智能制造业报道 - 今日头条"],
        ".article-content": [],
        "article": [],
        "body ::text": ["正文段落,讲述人工智能在制造业落地的进展与数据。"],
    })
    recorder = FetchRecorder({
        "https://www.toutiao.com/ch/news_tech/": listing,
        "https://www.toutiao.com/article/111/": article,
    })
    monkeypatch.setattr(fetcher, "fetch_page", recorder)

    source = SimpleNamespace(
        url="https://www.toutiao.com/ch/news_tech/",
        rate_limit_s=0.0,
        max_articles=5,
    )
    session = FakeSession()
    rules = FakeRules(disallow=("/article/333",))
    collected = toutiao.collect(source, limit=2, rules=rules, session=session)

    urls = [c["url"] for c in recorder.calls]
    assert urls == [
        "https://www.toutiao.com/ch/news_tech/",
        "https://www.toutiao.com/article/111/",
    ]
    assert all(c["session"] is session for c in recorder.calls)  # 全程共用同一会话
    assert collected["robots_skipped"] == 1
    assert collected["errors"] == 0
    assert collected["links"] == 2
    assert collected["pages"] == 2  # 列表页 + 1 篇文章:观测字段不虚报


# ---------------------------------------------------------------- 任务生命周期

def test_run_queued_source_missing_aborts_not_requeued(monkeypatch, system_db):
    write_sources(Path(system_db).parent, monkeypatch, [html_source_entry()])
    ghost = store.create_crawl_job("no_such_source", "demo", db_path=system_db)

    results = runner.run_queued(limit=10)
    assert any(r["job_id"] == ghost and r["error"] == "source_missing" for r in results)
    row = job_status(system_db, ghost)
    assert row["status"] == "aborted"

    # 终态:requeue 与 stale 恢复都不得把它复活 → 幽灵任务不再无限重排
    store.requeue_failed_crawl_jobs(db_path=system_db)
    store.recover_stale_crawl_jobs(db_path=system_db)
    assert job_status(system_db, ghost)["status"] == "aborted"


def test_run_queued_disabled_source_aborts(monkeypatch, system_db):
    write_sources(Path(system_db).parent, monkeypatch, [html_source_entry(id="off_src", enabled=False)])
    job_id = store.create_crawl_job("off_src", "demo", db_path=system_db)
    results = runner.run_queued(limit=10)
    assert any(r["job_id"] == job_id and r["error"] == "source_disabled" for r in results)
    assert job_status(system_db, job_id)["status"] == "aborted"
    store.requeue_failed_crawl_jobs(db_path=system_db)
    assert job_status(system_db, job_id)["status"] == "aborted"


def test_transient_fail_requeue_respects_max_attempts(system_db):
    job_id = store.create_crawl_job("flaky", "demo", db_path=system_db)
    for _ in range(3):
        store.mark_job_started(job_id, db_path=system_db)
        store.fail_crawl_job(job_id, "网络抖动", db_path=system_db)
        store.requeue_failed_crawl_jobs(db_path=system_db)
    row = job_status(system_db, job_id)
    assert row["status"] == "failed"  # 第 3 次失败后不再复活
    assert row["attempts"] == 3


# ---------------------------------------------------------------- 磁盘治理

def test_housekeep_report_covers_tools_and_pytest_buckets():
    report = housekeep.report()
    sizes = report["sizes_bytes"]
    assert "tools" in sizes and "pytest_of" in sizes
    # total 舍入到 3 位 GB,与桶求和允许 0.002 的舍入差
    assert report["project_total_gb"] + 2e-3 >= sum(sizes.values()) / (1024**3)
    for key in ("pip_cache_bytes",):
        assert key in report["caps_status"]


def test_prune_pytest_dirs_keeps_newest(tmp_path):
    for i, offset in enumerate((0, 100, 200, 300)):
        d = tmp_path / f"pytest-of-x{i}"
        d.mkdir()
        (d / "f").write_text("x", encoding="utf-8")
        st = d.stat()
        import os as _os

        _os.utime(d, (st.st_atime, st.st_mtime + offset))
    result = housekeep.prune_pytest_dirs(base=tmp_path, keep=2)
    assert result["removed"] == 2
    remaining = sorted(p.name for p in tmp_path.glob("pytest-of-*"))
    assert remaining == ["pytest-of-x2", "pytest-of-x3"]


# ---------------------------------------------------------------- Playwright 临时目录

def test_open_session_pins_playwright_tmp_to_project(tmp_path, monkeypatch):
    """真实根因:Playwright 在系统 %TEMP% mkdtemp 会 EPERM 且违反项目约束。

    用 DummySession 顶掉 scrapling 的 StealthySession(不启动真实浏览器),
    验证 _open_session 把 TMP/TEMP/TMPDIR 钉到 D:\\ttxt 项目内目录。
    """
    import scrapling.fetchers as sf

    started: list[bool] = []

    class DummySession:
        def start(self):
            started.append(True)

        def close(self):
            pass

    monkeypatch.setattr(sf, "StealthySession", lambda **kw: DummySession())
    monkeypatch.setenv("TMP", str(tmp_path / "system-temp"))
    monkeypatch.setenv("TEMP", str(tmp_path / "system-temp"))
    monkeypatch.delenv("TMPDIR", raising=False)

    session = fetcher._open_session()
    assert started == [True]
    for var in ("TMP", "TEMP", "TMPDIR"):
        value = Path(os.environ[var])
        assert str(value).startswith(str(cfg.CRAWLER_DIR.resolve()))
    session.close()


# ---------------------------------------------------------------- 观测诚实性

def test_run_queued_persists_honest_elapsed_and_pages(tmp_path, monkeypatch, system_db, fake_insert):
    """meta_json 必须携带真实的 pages_fetched 与 elapsed_s。

    真实缺陷(2026-11-12 job 13):toutiao 分支落库 pages_fetched=0(实际抓 12 页);
    elapsed 在 finish_crawl_job 之后才计算,meta_json 恒为初始 0.0。
    """
    write_sources(tmp_path, monkeypatch, [html_source_entry()])
    job = store.create_crawl_job("fake_html", "demo", db_path=system_db)
    recorder = FetchRecorder({"https://demo.example.com/page1/": quote_page()})
    monkeypatch.setattr(fetcher, "fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    # _execute_job 调用 monotonic 至少 3 次(started / 落库前 / finally),取值耗尽时钉在末值
    ticks = iter([100.0] + [107.5] * 10)
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(ticks))

    results = runner.run_queued(limit=5)
    assert results and results[0]["ok"] is True
    row = job_status(system_db, job)
    assert row["status"] == "done"
    meta = json.loads(row["meta_json"])
    assert meta["pages_fetched"] == 1
    assert meta["elapsed_s"] == 7.5


# ---------------------------------------------------------------- RSS 配额封顶

def test_robots_unknown_job_requeued_not_terminal(monkeypatch, system_db):
    """P12批次3真实缺陷(job 26 who_cn_news):robots.txt 瞬时失败被钉成终态 failed。

    robots_unknown 是 transient:attempts 未达上限必须回到 queued 等待重试;
    达到上限才落 failed。任何情况下都不得放行抓取(fail-safe 不变)。
    """
    write_sources(Path(system_db).parent, monkeypatch, [html_source_entry()])
    job = store.create_crawl_job("fake_html", "demo", db_path=system_db)
    monkeypatch.setattr(
        robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules(unknown=True)
    )

    results = runner.run_queued(limit=1)
    assert results and results[0]["error"] == "robots_unknown"
    assert results[0]["requeued"] is True
    row = job_status(system_db, job)
    assert row["status"] == "queued"
    assert row["attempts"] == 1  # 未抓取任何内容:失败发生在门禁阶段

    # 达到上限后必须落终态,不能无限重排(attempts 在认领时 +1)
    for _ in range(3):
        runner.run_queued(limit=1)
    row2 = job_status(system_db, job)
    assert row2["status"] == "failed"
    assert row2["attempts"] == 3


def test_rss_source_max_articles_caps_full_feed(tmp_path, monkeypatch, system_db, fake_insert):
    """P12批次3:RSS 全量 feed 可达数百条(2026-11-12 实测 eastmoney 96 条/次)。

    未显式传 limit 时必须按来源 max_articles 封顶,并把 feed_total 写进观测,
    防止单个源一次灌满类目库。
    """
    items_xml = "".join(
        f"<item><title>标题{i}</title><link>https://demo.example.com/a/{i}/</link>"
        f"<description>正文内容{i}</description>"
        f"<pubDate>Wed, 12 Nov 2026 10:00:0{i} +0800</pubDate></item>"
        for i in range(6)
    )
    feed = f"<?xml version='1.0'?><rss version='2.0'><channel>{items_xml}</channel></rss>"
    entry = html_source_entry(
        id="fake_rss", kind="rss", url="https://demo.example.com/feed.xml", max_articles=2
    )
    write_sources(tmp_path, monkeypatch, [entry])
    job = store.create_crawl_job("fake_rss", "demo", db_path=system_db)
    recorder = FetchRecorder({"https://demo.example.com/feed.xml": SimpleNamespace(text=feed)})
    monkeypatch.setattr(fetcher, "fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())

    results = runner.run_queued(limit=1)
    assert results and results[0]["ok"] is True
    assert results[0]["feed_total"] == 6  # 观测记录 feed 全量,不谎报
    assert results[0]["items_extracted"] == 2  # 实际入库按来源配额封顶
    assert len(fake_insert[0][2]) == 2
    meta = json.loads(job_status(system_db, job)["meta_json"])
    assert meta["feed_total"] == 6 and meta["inserted"] == 2
