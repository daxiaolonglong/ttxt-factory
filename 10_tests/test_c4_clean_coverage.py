"""C-4:采集入口必须自带清洗管线,并且体检要能看出清洗覆盖率落后。

实测根因(P15):恢复真实采集后普查 6 个命名空间 328 行,只有 43 行有清洗文本(13%)。
清洗不是跑不动(全量 328 行实测 4.3 秒),而是**采集入口从来没调用管线**。
这里钉三件事:
1. 采集成功后必须立刻跑清洗管线(派生字段不留尾巴);
2. 管线失败绝不能吞掉已落库的文章,也不能反过来让采集判失败;
3. 覆盖率低于 100% 时体检必须 WARN 并给出补跑命令。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from ttxt import config as cfg, db, doctor
from ttxt.crawler import extract, fetcher, pipeline, robots, runner, sources, store


class FakeRules:
    def __init__(self):
        self.unknown = False
        self.crawl_delay_s = None

    def allowed(self, path: str) -> bool:
        return True


class FetchRecorder:
    def __init__(self, pages: dict[str, object]):
        self.pages = pages

    def __call__(self, url, timeout=None, render=False, session=None):
        return SimpleNamespace(page=self.pages[url], status=200, url=url, elapsed_s=0.0)


def _feed(link: str, title: str, body: str) -> SimpleNamespace:
    xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<item><title>{title}</title><link>{link}</link><description>{body}</description>"
        "<pubDate>Tue, 11 Nov 2025 08:00:00 +0800</pubDate></item>"
        "</channel></rss>"
    )
    return SimpleNamespace(text=xml)


def _rss_entry(**overrides) -> dict:
    entry = {
        "id": "c4_rss", "name": "c4 rss", "kind": "rss", "category": "demo",
        "url": "https://demo.example.com/feed.xml", "enabled": True, "render": "http",
        "rate_limit_s": 0.0, "max_pages": 1, "max_articles": 20, "demo": True,
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def system_db(tmp_path, monkeypatch):
    path = tmp_path / "system.db"
    db.init_system_db(path)
    monkeypatch.setattr(store, "system_db_path", lambda: path)
    return path


@pytest.fixture
def categories_root(tmp_path, monkeypatch):
    categories = tmp_path / "01_categories"
    categories.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", categories)
    return categories


@pytest.fixture
def one_rss_crawl(tmp_path, monkeypatch, system_db, categories_root):
    """把"一次真实形态的 RSS 采集"搭好,返回 (articles_db, fetch_page 之前的页)。"""
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps({"sources": [_rss_entry()]}), encoding="utf-8")
    monkeypatch.setattr(sources, "SOURCES_PATH", sources_path)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    monkeypatch.setattr(
        fetcher,
        "fetch_page",
        FetchRecorder({
            "https://demo.example.com/feed.xml": _feed(
                "https://demo.example.com/a/1/",
                "美股三大指数涨跌不一 英伟达大跌",
                "英伟达大跌13%,市值蒸发超5000亿美元。",
            )
        }),
    )
    store.create_crawl_job("c4_rss", "demo", db_path=system_db)
    return categories_root / "demo" / "articles.db"


def _rows(articles_db):
    con = sqlite3.connect(articles_db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute("SELECT * FROM articles ORDER BY id")]
    finally:
        con.close()


# ------------------------------------------------------------------ 采集后必须清洗
def test_crawl_runs_cleaning_pipeline_after_insert(one_rss_crawl):
    runs = runner.run_queued(limit=1)
    assert runs and runs[0]["ok"] is True, runs[0]
    assert runs[0]["inserted"] == 1, runs[0]
    stats = runs[0]["pipeline"]
    assert stats["cleaned"] == 1
    assert stats["clean_version"] == pipeline.CLEAN_VERSION

    rows = _rows(one_rss_crawl)
    assert rows[0]["cleaned_content"]
    assert rows[0]["text_length"] > 0
    assert rows[0]["clean_version"] == pipeline.CLEAN_VERSION
    assert rows[0]["cleaned_at"]
    assert rows[0]["cluster_id"] is not None  # 聚类也跟着跑起来了,不是只填清洗文本


def test_crawl_summary_and_job_meta_carry_pipeline_stats(one_rss_crawl, system_db):
    runs = runner.run_queued(limit=1)
    assert "pipeline_error" not in runs[0]
    con = sqlite3.connect(system_db)
    con.row_factory = sqlite3.Row
    try:
        meta = json.loads(con.execute("SELECT meta_json FROM crawl_jobs ORDER BY id DESC LIMIT 1").fetchone()[0])
    finally:
        con.close()
    assert meta["pipeline"]["cleaned"] == 1
    assert meta["ok"] is True


# ------------------------------------------------------------------ 管线失败不能吞数据
def test_pipeline_failure_keeps_crawl_ok_and_rows_installed(one_rss_crawl, monkeypatch):
    def boom(category, db_path=None):
        raise RuntimeError("清洗管线故意炸")

    monkeypatch.setattr(runner.pipeline, "run_pipeline", boom)
    runs = runner.run_queued(limit=1)
    assert runs and runs[0]["ok"] is True
    assert "清洗管线故意炸" in runs[0]["pipeline_error"]
    # 关键:文章必须还在,派生字段只是没填 —— 采集结论不被清洗拖垮
    rows = _rows(one_rss_crawl)
    assert len(rows) == 1
    # 清洗列是管线里 migrate 才补上的:管线没跑成功,列都还不存在
    assert "cleaned_content" not in rows[0]


# ------------------------------------------------------------------ 体检要看得见覆盖率
def test_cleaned_coverage_flags_never_cleaned_namespace(one_rss_crawl, monkeypatch):
    """模拟绕过采集入口的老采集:行进了库,清洗从没跑过 → 体检必须点名这个命名空间。"""
    # 注意:绝不能用 monkeypatch.undo() 来还原管线 —— 那会把类目根等所有隔离一起撤掉,
    # 后面的补跑就会写到生产库上(P15 实测踩过一次,被 session 级生产指纹守卫拦下)。
    real_run_pipeline = pipeline.run_pipeline
    monkeypatch.setattr(runner.pipeline, "run_pipeline", lambda category, db_path=None: {})
    runs = runner.run_queued(limit=1)
    assert runs and runs[0]["ok"] is True, runs[0]

    level, name, detail = doctor._cleaned_coverage_row()
    assert (level, name) == ("WARN", "db.cleaned_coverage")
    assert "完全没有清洗列" in detail
    assert "demo" in detail and "ttxt clean --category demo" in detail

    monkeypatch.setattr(runner.pipeline, "run_pipeline", real_run_pipeline)  # 只还原被测函数
    pipeline.run_pipeline("demo")
    level, _, detail = doctor._cleaned_coverage_row()
    assert level == "PASS"
    assert "1/1" in detail


def test_cleaned_coverage_flags_partially_cleaned_namespace(one_rss_crawl):
    """管线跑过(列有了),之后又绕过入口塞一条 → 必须按 1/2 报出来,不能装看不见。"""
    runs = runner.run_queued(limit=1)
    assert runs and runs[0]["ok"] is True, runs[0]
    store.insert_items(
        "demo",
        "c4_rss",
        [
            extract.ExtractedItem(
                title="后来塞进来的标题",
                content="这条绕过了采集入口,所以没有清洗文本。",
                excerpt="",
                author=None,
                published_at=None,
                url="https://demo.example.com/bypass/1/",
            )
        ],
        page_url="https://demo.example.com/feed.xml",
    )
    level, name, detail = doctor._cleaned_coverage_row()
    assert (level, name) == ("WARN", "db.cleaned_coverage")
    assert "demo 1/2" in detail and "ttxt clean" in detail


def test_cleaned_coverage_warns_on_empty_corpus(categories_root):
    level, name, detail = doctor._cleaned_coverage_row()
    assert (level, name) == ("WARN", "db.cleaned_coverage")
    assert "没有文章" in detail


def test_completeness_section_includes_coverage_row(categories_root, monkeypatch):
    """C-1 那段体检现在必须带上覆盖率行,否则"标签都对但没人清洗"照样看不见。"""
    rows = {name: level for level, name, _ in doctor.completeness_checks()}
    assert "db.cleaned_coverage" in rows


def test_migrate_articles_db_refuses_unresolved_target(monkeypatch):
    """路径没解析出来就当场失败,绝不拿 None 去建库。

    实测教训:C-4 把管线串进采集入口后,p12 里 ``init_articles_db`` 的桩返回 None,
    迁移就 ``sqlite3.connect(str(None))``,在仓库根造出一个 4096 字节的 ``None`` 库文件。
    桩换成返回真实路径固然对,但产品侧也不能再信任"随便什么返回值"。
    """
    monkeypatch.setattr(store, "init_articles_db", lambda category, db_path=None: None)
    with pytest.raises(TypeError, match="没有返回数据库路径"):
        store.migrate_articles_db("demo")
    assert not (Path.cwd() / "None").exists(), "迁移在当前位置造出了计划外的库文件"
