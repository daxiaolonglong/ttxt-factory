"""C-7 历史遗留"同一篇文章存了两行"的整改:验收测试(先写测试,后写实现)。

实测依据(2026-11-15 只读取证,完整账本 ``07_tmp/p16_dedupe_20261115/census.json``):

- ``technology``:同一 URL ``https://www.toutiao.com/article/7486434702922531379/`` 有 **3 行**,
  标题完全相同("抖音2025年3月侵权举报受理公示"),正文长度 1967 / 1389 / 2787 —— 同一篇文章
  被三次渲染抽取,质量不同。这是真重复。
- ``finance``:同一 URL ``finance.eastmoney.com/news/1345,202611123929972290.html`` 有 **2 行**,
  标题不同(同一篇新闻被更新过),正文 11155 / 11852 —— 也是同一文章身份位上的两行。
- ``demo`` ↔ ``technology``:**13 行同 URL 同 content_hash** 的真拷贝(P12 期间来源由 demo 转正)。
- ``demo``:30 行共用 ``https://quotes.toscrape.com/``,但**标题与正文各不相同** —— 那是 html
  列表页抽取器把来源页地址赋给每一条(``extract.py:55``),``insert_items`` 已经把来源页地址
  排除在 URL 身份之外(``store.py`` C-2 注释),这 30 行**不是重复,绝不能删**。

由此确定判定原则:

1. 同一类目库内,同一规范化 URL = 同一篇文章身份 —— 但**来源页/订阅地址不是文章身份**
   (与 ``insert_items`` 的 ``page_url`` 口径一致),按它判重会毁掉列表页语料。
2. 只有"同一篇文章"才删:同 URL 组内标题相同或 content_hash 相同才算同一篇;
   标题与正文都不同 → 只报 ``identity_suspect``,不动数据。
3. 保留哪一行:正文最完整的那一行(``text_length`` 最大,其次 ``completeness``,再按最小 id),
   因为语料是给写作用的;其余行整行进账本,可逐行回滚。
4. 删行必须同时处理引用(标签/指标/快照/证据链/FTS),否则留下孤儿引用。
5. 跨命名空间:同一 ``(url_normalized, content_hash)`` 被多个命名空间持有时,
   **只有注册类目有所有权**;未注册命名空间(``demo``)的拷贝可删;
   两个注册类目都持有 → 只报 ``ambiguous``,不自动删(归属是人的决定)。
6. 默认干跑,``--apply`` 才写;写之前逐行 CAS,行被改过就整笔回滚(与 C-1 同一套做法)。
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from ttxt import config as cfg
from ttxt import doctor
from ttxt.crawler import dedupe, store

TECH_URL = "https://www.toutiao.com/article/7486434702922531379/"
PAGE_URL = "https://quotes.toscrape.com/"
OTHER_URL = "http://finance.eastmoney.com/news/1345,202611123929972290.html"


# --------------------------------------------------------------------- 构造辅助


def _insert(
    db_path,
    category: str,
    source: str,
    title: str,
    content: str,
    url: str,
    *,
    completeness: str = "full",
    text_length: int | None = None,
    hash_value: str | None = None,
    fetched_at: str | None = None,
    cluster_id: str | None = None,
) -> int:
    """直写一行(绕开写入侧判定):复现"修复之前已经落库"的历史数据。"""
    con = sqlite3.connect(str(db_path))
    try:
        cursor = con.execute(
            "INSERT INTO articles(category, source, source_item_id, title, content, excerpt,"
            " author, published_at, url, url_normalized, fetched_at, content_hash, completeness,"
            " credibility, processor_version, text_length, cluster_id) VALUES (?, ?, NULL, ?, ?, '',"
            " NULL, NULL, ?, ?, ?, ?, ?, 'unverified', ?, ?, ?)",
            (
                category,
                source,
                title,
                content,
                url,
                store.normalize_url(url),
                fetched_at or cfg.now_iso(),
                hash_value or store.content_hash(title, content),
                completeness,
                store.PROCESSOR_VERSION,
                text_length,
                cluster_id,
            ),
        )
        con.commit()
        return int(cursor.lastrowid)
    finally:
        con.close()


def _fetch_ids(db_path) -> list[int]:
    con = sqlite3.connect(str(db_path))
    try:
        return [row[0] for row in con.execute("SELECT id FROM articles ORDER BY id")]
    finally:
        con.close()


def _row(db_path, article_id: int) -> dict:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        con.close()


def _scalar(db_path: object, sql: str, params: tuple = ()) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return int(con.execute(sql, params).fetchone()[0])
    finally:
        con.close()


@pytest.fixture
def categories_root(tmp_path, monkeypatch):
    """隔离的类目根 + 注册表:注册 ``technology``,``demo`` 故意不注册(与生产一致)。"""
    root = tmp_path / "01_categories"
    root.mkdir(parents=True, exist_ok=True)
    registry = tmp_path / "category_registry.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "isolation": "physical",
                "categories": [
                    {"slug": "technology", "display_name": "科技", "status": "active"},
                    {"slug": "finance", "display_name": "财经", "status": "active"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", root)
    monkeypatch.setattr(store.cfg, "CATEGORIES_DIR", root)
    monkeypatch.setattr(cfg, "REGISTRY_PATH", registry)
    # 来源页地址:只留 quotes_toscrape 这一条,验证"来源页不是文章身份"这条口径
    monkeypatch.setattr(
        dedupe,
        "load_page_urls",
        lambda: [SimpleNamespace(url=PAGE_URL, id="quotes_toscrape")],
    )
    return root


def _db(root, slug: str):
    path = store.init_articles_db(slug)
    store.migrate_articles_db(slug, db_path=path)
    return path


# ----------------------------------------------------------------- 判重(纯函数)


def test_same_url_same_title_is_a_duplicate_group(categories_root):
    path = _db(categories_root, "technology")
    ids = [
        _insert(path, "technology", "toutiao_tech", "抖音公示", "短的" * 10, TECH_URL, text_length=1389),
        _insert(path, "technology", "toutiao_tech", "抖音公示", "中" * 100, TECH_URL, text_length=1967),
        _insert(path, "technology", "toutiao_tech", "抖音公示", "长" * 300, TECH_URL, text_length=2787),
    ]
    plan = dedupe.classify([_row(path, one) for one in ids], set())
    assert len(plan["duplicate_groups"]) == 1
    group = plan["duplicate_groups"][0]
    # 保留最完整的一行,其余进待删账本
    assert group["keeper_id"] == ids[2]
    assert group["removed_ids"] == [ids[0], ids[1]]
    assert group["reason"]


def test_source_page_url_is_never_a_duplicate(categories_root):
    path = _db(categories_root, "technology")
    ids = [
        _insert(path, "technology", "quotes_toscrape", f"名言 {n}", f"内容 {n}" * (n + 1), PAGE_URL)
        for n in range(5)
    ]
    plan = dedupe.classify([_row(path, one) for one in ids], {store.normalize_url(PAGE_URL)})
    assert plan["duplicate_groups"] == []
    assert len(plan["identity_suspect"]) == 1
    assert "来源页" in plan["identity_suspect"][0]["reason"]
    assert set(plan["identity_suspect"][0]["ids"]) == set(ids)


def test_same_url_different_title_and_body_is_reported_not_removed(categories_root):
    path = _db(categories_root, "technology")
    ids = [
        _insert(path, "technology", "eastmoney_rss", "英伟达大跌13%", "正文一" * 400, OTHER_URL, text_length=11155),
        _insert(path, "technology", "eastmoney_rss", "英伟达跌幅收窄", "正文二" * 420, OTHER_URL, text_length=11852),
    ]
    plan = dedupe.classify([_row(path, one) for one in ids], set())
    assert plan["duplicate_groups"] == []
    assert len(plan["identity_suspect"]) == 1
    assert "标题" in plan["identity_suspect"][0]["reason"]


def test_same_url_same_cluster_is_a_duplicate_group(categories_root):
    """生产实测口径:``finance`` 那两行标题不同(同一篇新闻被更新过),
    但清洗后同属一簇(``census.json`` 里 ``near_duplicate_pairs=1`` 就是这一对)。
    判重依据来自 simhash 聚类结果,不是猜标题。"""
    path = _db(categories_root, "finance")
    older = _insert(path, "finance", "eastmoney_rss", "英伟达大跌13%", "正文一" * 400, OTHER_URL,
                    text_length=11155, cluster_id="c000001", fetched_at="2026-11-12T09:00:00+08:00")
    newer = _insert(path, "finance", "eastmoney_rss", "英伟达跌幅收窄", "正文二" * 420, OTHER_URL,
                    text_length=11852, cluster_id="c000001", fetched_at="2026-11-12T18:00:00+08:00")
    plan = dedupe.classify([_row(path, older), _row(path, newer)], set())
    assert len(plan["duplicate_groups"]) == 1
    assert plan["duplicate_groups"][0]["keeper_id"] == newer
    assert plan["duplicate_groups"][0]["removed_ids"] == [older]
    assert "同属一簇" in plan["duplicate_groups"][0]["reason"]


def test_equal_length_group_keeps_full_then_oldest(categories_root):
    """保留哪一行:正文长度 → ``completeness=full`` → 最小 id。

    注:单库内 ``content_hash`` 有唯一索引(生产实测 0 组同 hash),所以"正文完全相同"
    只会出现在跨命名空间;单库内的同篇文章是"同标题不同正文"(渲染/抽取质量不同)。
    """
    path = _db(categories_root, "technology")
    thin = _insert(path, "technology", "s", "同一篇", "正文甲" * 100, TECH_URL, completeness="excerpt",
                   text_length=300, fetched_at="2026-01-01T00:00:00+08:00")
    full = _insert(path, "technology", "s", "同一篇", "正文乙" * 100, TECH_URL, completeness="full",
                   text_length=300, fetched_at="2026-01-02T00:00:00+08:00")
    plan = dedupe.classify([_row(path, thin), _row(path, full)], set())
    assert plan["duplicate_groups"][0]["keeper_id"] == full
    assert plan["duplicate_groups"][0]["removed_ids"] == [thin]

    tie = _insert(path, "technology", "s", "同一篇", "正文丙" * 100, TECH_URL, completeness="full",
                  text_length=300, fetched_at="2026-01-03T00:00:00+08:00")
    plan = dedupe.classify([_row(path, thin), _row(path, full), _row(path, tie)], set())
    assert plan["duplicate_groups"][0]["keeper_id"] == full
    assert plan["duplicate_groups"][0]["removed_ids"] == sorted([thin, tie])


def test_fuller_body_wins_even_when_it_is_the_newer_row(categories_root):
    path = _db(categories_root, "technology")
    thin = _insert(path, "technology", "s", "同一篇", "旧但短" * 30, TECH_URL, text_length=90,
                   fetched_at="2026-01-01T00:00:00+08:00")
    full = _insert(path, "technology", "s", "同一篇", "新且全" * 900, TECH_URL, text_length=2700,
                   fetched_at="2026-01-09T00:00:00+08:00")
    plan = dedupe.classify([_row(path, thin), _row(path, full)], set())
    assert plan["duplicate_groups"][0]["keeper_id"] == full


# ------------------------------------------------------------- 计划:只读、不改数据


def test_plan_does_not_touch_the_database(categories_root):
    path = _db(categories_root, "technology")
    for one in range(3):
        _insert(path, "technology", "toutiao_tech", "抖音公示", "正文" * (one + 1) * 30, TECH_URL,
                text_length=(one + 1) * 90)
    before = _fetch_ids(path)
    before_sha = path.read_bytes()
    plan = dedupe.plan_category("technology")
    assert plan["removable"] == 2
    assert _fetch_ids(path) == before
    assert path.read_bytes() == before_sha, "干跑不得写库"


# ---------------------------------------------------------------- 写:删除与引用


def _seed_three_tech_rows(path):
    return [
        _insert(path, "technology", "toutiao_tech", "抖音公示", "甲" * 300, TECH_URL, text_length=1967),
        _insert(path, "technology", "toutiao_tech", "抖音公示", "乙" * 200, TECH_URL, text_length=1389),
        _insert(path, "technology", "toutiao_tech", "抖音公示", "丙" * 900, TECH_URL, text_length=2787),
    ]


def test_apply_removes_rows_and_ledger_keeps_full_snapshots(categories_root):
    path = _db(categories_root, "technology")
    ids = _seed_three_tech_rows(path)
    original = [_row(path, one) for one in ids[:2]]
    result = dedupe.apply_plan(dedupe.plan_category("technology"))
    assert result["removed"] == 2
    assert sorted(_fetch_ids(path)) == [ids[2]]
    ledger = {int(row["id"]): row for row in result["ledger"]}
    assert set(ledger) == {ids[0], ids[1]}
    for article_id, snapshot in ledger.items():
        for column, value in original[0 if article_id == ids[0] else 1].items():
            assert snapshot[column] == value, f"账本没留全 {column}"


def test_labels_move_to_keeper_when_keeper_has_none(categories_root):
    path = _db(categories_root, "technology")
    ids = _seed_three_tech_rows(path)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "INSERT INTO viral_labels(article_id, label, confidence_tier, score, labeled_at)"
            " VALUES (?, '爆款', 'high', 0.9, ?)",
            (ids[0], cfg.now_iso()),
        )
        con.commit()
    finally:
        con.close()
    dedupe.apply_plan(dedupe.plan_category("technology"))
    con = sqlite3.connect(str(path))
    try:
        rows = con.execute("SELECT article_id, label FROM viral_labels").fetchall()
    finally:
        con.close()
    assert rows == [(ids[2], "爆款")], "分析结果必须跟着活下来的那一行,不能变孤儿"


def test_keeper_already_has_label_so_dup_label_is_dropped_and_recorded(categories_root):
    path = _db(categories_root, "technology")
    ids = _seed_three_tech_rows(path)
    con = sqlite3.connect(str(path))
    try:
        for article_id, tier in ((ids[0], "high"), (ids[2], "low")):
            con.execute(
                "INSERT INTO viral_labels(article_id, label, confidence_tier, score, labeled_at)"
                " VALUES (?, '爆款', ?, 0.5, ?)",
                (article_id, tier, cfg.now_iso()),
            )
        con.commit()
    finally:
        con.close()
    result = dedupe.apply_plan(dedupe.plan_category("technology"))
    con = sqlite3.connect(str(path))
    try:
        kept = con.execute("SELECT article_id, confidence_tier FROM viral_labels").fetchall()
    finally:
        con.close()
    assert kept == [(ids[2], "low")]
    dropped = [item for item in result["dropped_references"] if item["table"] == "viral_labels"]
    assert len(dropped) == 1 and int(dropped[0]["row"]["article_id"]) == ids[0]


def test_evidence_links_repointed_and_fts_rows_removed(categories_root):
    path = _db(categories_root, "technology")
    ids = _seed_three_tech_rows(path)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "INSERT INTO evidence_links(draft_id, claim, article_id, quote, created_at)"
            " VALUES ('d1', '断言', ?, '引文', ?)",
            (ids[0], cfg.now_iso()),
        )
        con.execute(
            "INSERT INTO metric_snapshots(article_id, captured_at, reads) VALUES (?, ?, 100)",
            (ids[1], cfg.now_iso()),
        )
        con.execute("INSERT INTO articles_fts(article_id, title, body) VALUES (?, ?, ?)", (ids[0], "抖音公示", "甲" * 300))
        con.commit()
    finally:
        con.close()
    dedupe.apply_plan(dedupe.plan_category("technology"))
    con = sqlite3.connect(str(path))
    try:
        evidence = con.execute("SELECT draft_id, article_id FROM evidence_links").fetchall()
        snapshots = con.execute("SELECT article_id FROM metric_snapshots").fetchall()
        fts = con.execute("SELECT article_id FROM articles_fts").fetchall()
    finally:
        con.close()
    assert evidence == [("d1", ids[2])], "证据链要改指向保留行,草稿的证据链不能断"
    assert [row[0] for row in snapshots] == [ids[2]], "真实指标快照要保下来"
    assert fts == [], "FTS 不能留着指向已删行的陈旧条目"


def test_apply_is_atomic_when_a_row_changed_after_the_plan(categories_root):
    path = _db(categories_root, "technology")
    ids = _seed_three_tech_rows(path)
    plan = dedupe.plan_category("technology")
    con = sqlite3.connect(str(path))
    try:
        con.execute("UPDATE articles SET content_hash = 'changed-after-plan' WHERE id = ?", (ids[0],))
        con.commit()
    finally:
        con.close()
    with pytest.raises(dedupe.DedupeConflict):
        dedupe.apply_plan(plan)
    assert sorted(_fetch_ids(path)) == sorted(ids), "CAS 失败必须整笔回滚,不能删一半"


def test_restore_puts_rows_and_references_back(categories_root):
    path = _db(categories_root, "technology")
    ids = _seed_three_tech_rows(path)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "INSERT INTO viral_labels(article_id, label, confidence_tier, score, labeled_at)"
            " VALUES (?, '爆款', 'high', 0.9, ?)",
            (ids[0], cfg.now_iso()),
        )
        con.execute("INSERT INTO articles_fts(article_id, title, body) VALUES (?, ?, ?)", (ids[0], "抖音公示", "甲" * 300))
        con.commit()
    finally:
        con.close()
    before = [_row(path, one) for one in ids]
    report = dedupe.apply_plan(dedupe.plan_category("technology"))
    assert len(_fetch_ids(path)) == 1
    restored = dedupe.rollback(report)
    assert restored["restored"] == 2
    after = [_row(path, one) for one in ids]
    for original, current in zip(before, after):
        for column, value in original.items():
            assert current[column] == value, f"回滚没恢复 {column}"
    con = sqlite3.connect(str(path))
    try:
        labels = con.execute("SELECT article_id, label FROM viral_labels").fetchall()
    finally:
        con.close()
    assert labels == [(ids[0], "爆款")]


# ------------------------------------------------------------------ 跨命名空间


def test_cross_copies_are_removable_only_from_the_namespace_without_ownership(categories_root):
    tech = _db(categories_root, "technology")
    demo = _db(categories_root, "demo")
    title, content = "阮一周五栏", "周刊正文" * 200
    url = "http://www.ruanyifeng.com/blog/2026/11/weekly-issue-418.html"
    keeper = _insert(tech, "technology", "ruanyifeng_atom", title, content, url, text_length=800)
    copy = _insert(demo, "demo", "ruanyifeng_atom", title, content, url, text_length=800)
    plan = dedupe.plan_cross()
    assert len(plan["copies"]) == 1
    assert plan["copies"][0]["removable"] == [{"category": "demo", "ids": [copy]}]
    assert plan["ambiguous"] == []
    result = dedupe.apply_cross(plan)
    assert _fetch_ids(demo) == []
    assert _fetch_ids(tech) == [keeper]
    assert result["removed"] == 1
    assert result["ledger"][0]["category"] == "demo"


def test_two_registered_holders_are_reported_not_auto_removed(categories_root):
    tech = _db(categories_root, "technology")
    finance = _db(categories_root, "finance")
    title, content = "跨类目的同一篇", "正文" * 200
    url = "https://example.com/post/1"
    _insert(tech, "technology", "s", title, content, url, text_length=600)
    _insert(finance, "finance", "s", title, content, url, text_length=600)
    plan = dedupe.plan_cross()
    assert plan["copies"] == []
    assert len(plan["ambiguous"]) == 1
    assert set(plan["ambiguous"][0]["holders"]) == {"technology", "finance"}
    assert len(_fetch_ids(tech)) == 1 and len(_fetch_ids(finance)) == 1, "归属是人的决定,机器不自动删"


def test_rollback_of_cross_removals(categories_root):
    tech = _db(categories_root, "technology")
    demo = _db(categories_root, "demo")
    title, content = "sspai 的一篇", "正文" * 150
    url = "https://sspai.com/post/118175"
    _insert(tech, "technology", "sspai_rss", title, content, url, text_length=450)
    copy = _insert(demo, "demo", "sspai_rss", title, content, url, text_length=450)
    report = dedupe.apply_cross(dedupe.plan_cross())
    assert _fetch_ids(demo) == []
    dedupe.rollback(report)
    assert _fetch_ids(demo) == [copy]


# ---------------------------------------------------------------------- 体检对账


def test_doctor_finds_duplicate_rows_and_passes_after_cleanup(categories_root):
    path = _db(categories_root, "technology")
    _seed_three_tech_rows(path)
    rows = {name: (level, detail) for level, name, detail in doctor.completeness_checks()}
    assert "db.duplicate_rows" in rows
    level, detail = rows["db.duplicate_rows"]
    assert level in ("FAIL", "WARN")
    assert TECH_URL in detail or "technology" in detail

    dedupe.apply_plan(dedupe.plan_category("technology"))
    rows = {name: (level, detail) for level, name, detail in doctor.completeness_checks()}
    assert rows["db.duplicate_rows"][0] == "PASS"


def test_doctor_explains_list_page_collapse_as_suspect(categories_root):
    path = _db(categories_root, "technology")
    for one in range(4):
        _insert(path, "technology", "quotes_toscrape", f"名言{one}", f"内容{one}" * (one + 3), PAGE_URL)
    rows = {name: (level, detail) for level, name, detail in doctor.completeness_checks()}
    level, detail = rows["db.identity_suspect"]
    assert level == "WARN"
    assert "列表页" in detail


def test_doctor_reference_integrity_passes_after_cleanup_and_fails_on_orphan(categories_root):
    """删行不得留下孤儿引用;真的孤儿必须 FAIL(这些表没有外键,没人会报错)。"""
    path = _db(categories_root, "technology")
    ids = _seed_three_tech_rows(path)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "INSERT INTO viral_labels(article_id, label, confidence_tier, score, labeled_at)"
            " VALUES (?, '爆款', 'high', 8, '2026-11-15T00:00:00+08:00')",
            (ids[2],),
        )
        con.execute(
            "INSERT INTO evidence_links(draft_id, claim, article_id, quote, created_at)"
            " VALUES (?, ?, ?, ?, '2026-11-15T00:00:00+08:00')",
            ("technology-1", "断言", None, None),
        )
        con.commit()
    finally:
        con.close()

    rows = {name: (level, detail) for level, name, detail in doctor.completeness_checks()}
    level, detail = rows["db.orphan_references"]
    assert level == "WARN", "article_id 为空的证据行只算取证不完整,不是断链"
    assert "article_id 为空" in detail

    dedupe.apply_plan(dedupe.plan_category("technology"))
    rows = {name: (level, detail) for level, name, detail in doctor.completeness_checks()}
    level, detail = rows["db.orphan_references"]
    assert level != "FAIL", detail
    assert "没有孤儿引用" in detail, detail

    con = sqlite3.connect(str(path))
    try:
        # 绕开判重工具直接删文章行:引用表没有外键,只有体检能发现
        keeper = _fetch_ids(path)[0]
        con.execute("DELETE FROM articles WHERE id = ?", (keeper,))
        con.commit()
    finally:
        con.close()
    rows = {name: (level, detail) for level, name, detail in doctor.completeness_checks()}
    level, detail = rows["db.orphan_references"]
    assert level == "FAIL"
    assert "technology.viral_labels" in detail


# ------------------------------------------------------------------------- 命令


def test_cli_dry_run_then_apply_then_rollback(categories_root, monkeypatch, capsys):
    from ttxt.__main__ import main as cli_main

    path = _db(categories_root, "technology")
    ids = _seed_three_tech_rows(path)
    report = categories_root / "c7_report.json"

    assert cli_main(["dedupe-history", "--category", "technology", "--out", str(report)]) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["applied"] is False
    assert len(_fetch_ids(path)) == 3, "干跑不得删行"

    assert cli_main(["dedupe-history", "--category", "all", "--apply", "--out", str(report)]) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["applied"] is True and payload["removed"] == 2
    assert _fetch_ids(path) == [ids[2]]

    assert cli_main(["dedupe-history", "--rollback", str(report)]) == 0
    assert sorted(_fetch_ids(path)) == sorted(ids)
