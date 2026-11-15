"""C-1 正文完整性判定(``completeness``):验收测试(先写测试,后改实现)。

缺陷实测依据(2026-11-15 只读核查,逐行普查见 ``07_tmp/c1_20261115/census_c1.json``):

- 6 个命名空间共 257 行,``completeness`` **全是 ``full``**(demo 45 / finance 50 /
  health 25 / technology 137),没有一行是 ``excerpt`` 或其它值;
  而“实际可用正文”长度分布是:p0=10、p10=60、p25=96、p50=615、p75=2471、max=26844,
  **110 行不足 200 字、124 行不足 400 字**(finance 甚至有 12 行只有 10–49 字)。
  也就是说 RSS 摘要、列表页正文、以及只抓到一段的行,都被当成“全文”记进了语料库。
- 根因在写入侧一行代码:``store.insert_items`` 写的是
  ``"full" if item.content else "excerpt"`` —— 只看“有没有正文”,不看“正文有多长”;
  而 DDL 默认值又是 ``completeness TEXT NOT NULL DEFAULT 'full'``(``store.py`` 建表语句),
  任何绕开 ``insert_items`` 的写入都会自动谎报全文。
- 全项目**没有任何地方读这个列**(grep ``completeness``:只有建表、INSERT 与测试夹具),
  所以错标签目前既没人用也没人发现 —— 违反核心规则 6“数据必须带 completeness 与 credibility”,
  而且一旦爆款分析/写作按“只用全文语料”筛选,这 124 行摘要会直接污染训练与取材口径。

由此确定本文件的判定原则(也是实现的判定原则):

1. 三档取值:``full`` = 可用正文 ≥ 400 字;``short`` = 有正文但 < 400 字;
   ``excerpt`` = 没有可用正文(只有标题/excerpt 列)。
   阈值 400 与写作门禁(``08_wiki/db_schema.md`` 关键语义:长度 ≥400 才可能 ``passed``)同轴,
   含义就是“这篇够不够当全文语料用”。
2. 判定必须由**同一个函数**给出(``cleaner.completeness_of``),写入侧、回填侧、体检侧不得各写一套。
3. 历史行必须能**如实整改**且可回滚:整改默认 dry-run,必须逐行给出 ``from → to`` 与长度证据,
   写入必须在单个 ``BEGIN IMMEDIATE`` 事务里带 CAS(旧值不符就整笔回滚,不做部分提交)。
4. 体检必须能发现谎报(``full`` 但长度不足 / ``excerpt`` 但明明有长正文 / 出现词表外取值),
   并在 DDL 默认值不改的前提下把矛盾喊出来(本批不做 DDL 迁移)。

不覆盖(明确属于其它任务):``credibility`` 分层(C-5)、历史重复行清理(C-3 遗留)、
清洗管线 ``text_length`` 列语义(仍为清洗后文本长度,含标题前缀,不在本批改)。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from ttxt import config as cfg
from ttxt import doctor
from ttxt.__main__ import main as cli_main
from ttxt.crawler import cleaner, extract, pipeline as crawler_pipeline, store

REGISTERED = ["technology", "finance", "health", "social", "history"]
DISPLAY = {
    "technology": "科技",
    "finance": "财经",
    "health": "健康",
    "social": "社会",
    "history": "历史",
}
VALUES = ("full", "short", "excerpt")


# --------------------------------------------------------------------- 通用辅助


def _item(title: str, content: str, url: str, excerpt: str = "") -> object:
    return extract.ExtractedItem(
        title=title,
        content=content,
        excerpt=excerpt,
        author=None,
        published_at=None,
        url=url,
        source_item_id=None,
    )


def _labels(db_path) -> list[dict]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in con.execute(
                "SELECT id, title, completeness, LENGTH(COALESCE(content,'')) AS content_len"
                " FROM articles ORDER BY id"
            )
        ]
    finally:
        con.close()


def _legacy_label(db_path, category: str, title: str, content: str, url: str, label: str) -> None:
    """直写一行并按指定 ``completeness`` 落库,绕开写入侧判定。

    用来复现两件事:DDL 默认值造成的谎报、以及“修复前”已经落库的历史脏标签。
    """
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO articles(category, source, source_item_id, title, content, excerpt,"
            " author, published_at, url, url_normalized, fetched_at, content_hash, completeness,"
            " credibility, processor_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                category,
                "legacy_source",
                None,
                title,
                content,
                "",
                None,
                None,
                url,
                store.normalize_url(url),
                cfg.now_iso(),
                store.content_hash(title, content),
                label,
                "unverified",
                store.PROCESSOR_VERSION,
            ),
        )
        con.commit()
    finally:
        con.close()


def _sha(path) -> tuple[int, str]:
    data = Path(path).read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


@pytest.fixture
def namespaces(tmp_path, monkeypatch):
    """空的类目根 + 自建注册表:注册 5 个正式类目,``demo`` 故意不注册(与生产一致)。"""
    root = tmp_path / "01_categories"
    root.mkdir(parents=True, exist_ok=True)
    registry = tmp_path / "category_registry.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "isolation": "physical",
                "categories": [
                    {"slug": slug, "display_name": DISPLAY[slug], "status": "active"}
                    for slug in REGISTERED
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", root)
    monkeypatch.setattr(cfg, "REGISTRY_PATH", registry)
    return root


# ------------------------------------------------ 1 判定函数:三档与阈值


def test_threshold_matches_writing_gate():
    """阈值必须与写作门禁(长度 ≥400)同轴,并能被测试钉住。"""
    assert cleaner.MIN_FULL_CHARS == 400


def test_no_content_is_excerpt():
    assert cleaner.completeness_of("") == "excerpt"
    assert cleaner.completeness_of(None) == "excerpt"  # type: ignore[arg-type]
    assert cleaner.completeness_of("   \n\t ") == "excerpt"


def test_short_content_is_short_below_threshold():
    assert cleaner.completeness_of("字") == "short"
    assert cleaner.completeness_of("字" * (cleaner.MIN_FULL_CHARS - 1)) == "short"


def test_content_at_or_above_threshold_is_full():
    assert cleaner.completeness_of("字" * cleaner.MIN_FULL_CHARS) == "full"
    assert cleaner.completeness_of("字" * 5000) == "full"


def test_leading_trailing_whitespace_does_not_count_toward_length():
    short = "字" * 20
    assert cleaner.completeness_of("\n\n  " + short + "  \n") == "short"
    assert cleaner.completeness_of("\n " + "字" * cleaner.MIN_FULL_CHARS + " \n") == "full"


# ------------------------------------------------ 2 写入侧:标签必须如实


def test_insert_items_labels_full_and_short_per_row(namespaces):
    """同批次里长文 ``full``、短摘要 ``short``,行数计数不受影响。"""
    items = [
        _item("长文标题", "正文" * 400, "https://a.example.com/long"),
        _item("摘要标题", "一句话摘要", "https://a.example.com/short"),
    ]
    result = store.insert_items("technology", "src_a", items)
    assert result["inserted"] == 2
    rows = _labels(store.articles_db_path("technology"))
    by_title = {row["title"]: row["completeness"] for row in rows}
    assert by_title == {"长文标题": "full", "摘要标题": "short"}


def test_insert_items_labels_excerpt_when_content_missing(namespaces):
    """只有摘要列有字、正文为空 → ``excerpt``(绝不因为“抓到了东西”就叫全文)。"""
    items = [_item("无正文", "", "https://a.example.com/empty", excerpt="别人写的摘要")]
    store.insert_items("technology", "src_a", items)
    rows = _labels(store.articles_db_path("technology"))
    assert [row["completeness"] for row in rows] == ["excerpt"]


def test_insert_items_whitespace_only_content_is_excerpt(namespaces):
    items = [_item("空白正文", "   \n\n  ", "https://a.example.com/blank")]
    store.insert_items("technology", "src_a", items)
    rows = _labels(store.articles_db_path("technology"))
    assert [row["completeness"] for row in rows] == ["excerpt"]


def test_inserted_labels_never_use_ddl_default(namespaces):
    """DDL 默认值是 ``full``;写入侧必须每行显式给值,不靠默认值兜。"""
    items = [_item("短", "只有一小段", "https://a.example.com/x")]
    store.insert_items("finance", "src_a", items)
    con = sqlite3.connect(str(store.articles_db_path("finance")))
    try:
        default = con.execute("PRAGMA table_info(articles)").fetchall()
        row_default = next(str(col[4]) for col in default if col[1] == "completeness")
        stored = con.execute("SELECT completeness FROM articles").fetchone()[0]
    finally:
        con.close()
    assert row_default.strip("'") == "full"  # 本批不做 DDL 迁移:默认值仍是隐患,由体检兜底
    assert stored == "short"


def test_cleaning_never_rewrites_collection_side_label(namespaces):
    """``completeness`` 是**采集侧**事实:清洗管线不得回头改写它。

    清洗会另算 ``cleaned_content``/``text_length``(含标题前缀,口径不同);如果清洗
    再把 completeness 改一遍,同一个词就有两种含义,下游(爆款分析、写作取材)无法稳定筛选。
    判定只看 ``content`` 列,始终由 ``cleaner.completeness_of`` 一处定义。
    """
    items = [
        _item("短摘要", "一句话摘要", "https://a.example.com/keep1"),
        _item("长文", "正文" * 300, "https://a.example.com/keep2"),
    ]
    store.insert_items("technology", "src_a", items)
    path = store.articles_db_path("technology")
    before = {row["title"]: row["completeness"] for row in _labels(path)}
    assert before == {"短摘要": "short", "长文": "full"}

    stats = crawler_pipeline.run_pipeline("technology")
    assert stats["cleaned"] == 2

    after = {row["title"]: row["completeness"] for row in _labels(path)}
    assert after == before
    assert store.relabel_completeness("technology")["changes"] == []


# ------------------------------------------------ 3 历史整改:干跑、写入、回滚


def test_relabel_plan_is_dry_run_and_changes_nothing(namespaces):
    """默认只出计划:逐行 from→to + 长度证据,库字节与 sha 一字不变。"""
    path = store.init_articles_db("technology")
    _legacy_label(path, "technology", "谎报全文", "只有 12 个字", "https://legacy.example.com/1", "full")
    _legacy_label(path, "technology", "真全文", "正文" * 400, "https://legacy.example.com/2", "full")
    before = _sha(path)

    plan = store.relabel_completeness("technology")
    assert plan["applied"] is False
    assert plan["threshold"] == cleaner.MIN_FULL_CHARS
    assert plan["rows_total"] == 2
    changes = plan["changes"]
    assert len(changes) == 1
    assert (changes[0]["from"], changes[0]["to"]) == ("full", "short")
    assert changes[0]["usable_len"] < cleaner.MIN_FULL_CHARS
    assert plan["current"] == {"full": 2}
    assert plan["planned"] == {"full": 1, "short": 1}
    assert _sha(path) == before
    assert [row["completeness"] for row in _labels(path)] == ["full", "full"]


def test_relabel_apply_rewrites_labels_and_is_idempotent(namespaces):
    path = store.init_articles_db("technology")
    _legacy_label(path, "technology", "谎报全文", "只有 12 个字", "https://legacy.example.com/1", "full")
    _legacy_label(path, "technology", "空白正文", "   \n ", "https://legacy.example.com/2", "full")
    _legacy_label(path, "technology", "真全文", "正文" * 400, "https://legacy.example.com/3", "full")

    applied = store.relabel_completeness("technology", apply=True)
    assert applied["applied"] is True
    assert applied["updated"] == 2
    assert [row["completeness"] for row in _labels(path)] == ["short", "excerpt", "full"]

    again = store.relabel_completeness("technology")
    assert again["changes"] == []
    assert store.relabel_completeness("technology", apply=True)["updated"] == 0
    assert _labels(path) == _labels(path)


def test_relabel_restore_returns_every_label_to_previous_value(namespaces):
    """整改必须可回滚:逐行 from→to 记下来后,restore 精确恢复旧值。"""
    path = store.init_articles_db("finance")
    _legacy_label(path, "finance", "谎报全文", "十个字而已", "https://legacy.example.com/r1", "full")
    _legacy_label(path, "finance", "短摘要", "一句话", "https://legacy.example.com/r2", "full")
    before = [row["completeness"] for row in _labels(path)]

    plan = store.relabel_completeness("finance", apply=True)
    assert [row["completeness"] for row in _labels(path)] == ["short", "short"]

    restored = store.restore_completeness("finance", plan["changes"])
    assert restored["restored"] == len(plan["changes"])
    assert [row["completeness"] for row in _labels(path)] == before


def test_relabel_apply_rejects_stale_plan_atomically(namespaces):
    """计划里的旧值与库中实际值不符 → 拒绝并整笔回滚(绝不部分提交、绝不覆盖别人的改动)。"""
    path = store.init_articles_db("health")
    _legacy_label(path, "health", "谎报全文", "只有几个字", "https://legacy.example.com/c1", "full")
    _legacy_label(path, "health", "另一条谎报", "也只有几个字", "https://legacy.example.com/c2", "full")
    plan = store.relabel_completeness("health")
    assert len(plan["changes"]) == 2

    con = sqlite3.connect(str(path))
    try:
        con.execute("UPDATE articles SET completeness = 'excerpt' WHERE url_normalized LIKE '%c1%'")
        con.commit()
    finally:
        con.close()

    with pytest.raises(store.CompletenessConflict):
        store.apply_completeness_plan("health", plan["changes"])

    rows = _labels(path)
    got = {row["title"]: row["completeness"] for row in rows}
    assert got["另一条谎报"] == "full"  # 计划里也要改它,但整笔回滚了
    assert got["谎报全文"] == "excerpt"  # 外部改动保持原样


def test_relabel_refuses_missing_database_without_creating_it(namespaces):
    """整改不得顺手建库(与 C-3 的“只读/不创建”纪律一致)。"""
    with pytest.raises(FileNotFoundError):
        store.relabel_completeness("technology")
    assert not (namespaces / "technology" / "articles.db").exists()


# ------------------------------------------------ 4 体检:必须能发现谎报


def _completeness_rows() -> list[tuple[str, str, str]]:
    return [row for row in doctor.completeness_checks()]


def test_doctor_warns_when_full_label_is_too_short(namespaces):
    path = store.init_articles_db("technology")
    _legacy_label(path, "technology", "谎报全文", "只有几个字", "https://legacy.example.com/d1", "full")
    _legacy_label(path, "technology", "真全文", "正文" * 400, "https://legacy.example.com/d2", "full")

    rows = _completeness_rows()
    levels = {name: (level, detail) for level, name, detail in rows}
    assert levels["db.completeness.technology"][0] == "WARN"
    assert "full=2" in levels["db.completeness.technology"][1]
    assert "谎报" in levels["db.completeness.technology"][1]


def test_doctor_passes_after_relabel(namespaces):
    path = store.init_articles_db("technology")
    _legacy_label(path, "technology", "谎报全文", "只有几个字", "https://legacy.example.com/d3", "full")
    store.relabel_completeness("technology", apply=True)

    levels = {name: (level, detail) for level, name, detail in _completeness_rows()}
    assert levels["db.completeness.technology"][0] == "PASS"


def test_doctor_warns_when_excerpt_hides_long_content(namespaces):
    path = store.init_articles_db("health")
    _legacy_label(path, "health", "明明有全文", "正文" * 400, "https://legacy.example.com/d4", "excerpt")
    levels = {name: (level, detail) for level, name, detail in _completeness_rows()}
    assert levels["db.completeness.health"][0] == "WARN"
    assert "excerpt" in levels["db.completeness.health"][1]


def test_doctor_fails_on_unknown_completeness_value(namespaces):
    """词表外取值把数据口径搞坏(下游无法按 completeness 筛选)→ FAIL。"""
    path = store.init_articles_db("social")
    _legacy_label(path, "social", "乱标", "随便一段正文", "https://legacy.example.com/d5", "全部")
    levels = {name: (level, detail) for level, name, detail in _completeness_rows()}
    assert levels["db.completeness.social"][0] == "FAIL"
    assert "全部" in levels["db.completeness.social"][1]


def test_doctor_lists_per_namespace_distribution(namespaces):
    path = store.init_articles_db("technology")
    _legacy_label(path, "technology", "真全文", "正文" * 400, "https://legacy.example.com/d6", "full")
    _legacy_label(path, "technology", "短", "短正文", "https://legacy.example.com/d7", "short")
    detail = {name: detail for level, name, detail in _completeness_rows()}["db.completeness.technology"]
    assert "full=1" in detail and "short=1" in detail and "excerpt=0" in detail


def test_run_doctor_includes_completeness_section(isolated_data_root, monkeypatch):
    """``ttxt doctor`` 里必须出现这一项,否则整改结果无人可见。

    用 ``isolated_data_root``(生产库/配置的 tmp 逐字节副本)跑真实的 ``run_doctor``,
    这样既证明了体检输出里能看到该检查,也不会碰生产库。
    """
    path = store.init_articles_db("technology")
    _legacy_label(path, "technology", "谎报全文", "几个字", "https://legacy.example.com/d8", "full")
    printed: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(str(x) for x in a)))
    assert doctor.run_doctor() in (0, 1)
    joined = "\n".join(printed)
    assert "db.completeness.technology" in joined


# ------------------------------------------------ 5 CLI:干跑默认,写入要显式


def test_cli_dry_run_by_default_and_apply_writes_report(namespaces, tmp_path, capsys):
    path = store.init_articles_db("technology")
    _legacy_label(path, "technology", "谎报全文", "只有几个字", "https://legacy.example.com/k1", "full")
    report = tmp_path / "relabel.json"

    assert cli_main(["relabel-completeness", "--category", "technology", "--out", str(report)]) == 0
    assert [row["completeness"] for row in _labels(path)] == ["full"]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["results"]["technology"]["applied"] is False

    assert cli_main(["relabel-completeness", "--category", "technology", "--apply", "--out", str(report)]) == 0
    assert [row["completeness"] for row in _labels(path)] == ["short"]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["results"]["technology"]["applied"] is True
    assert payload["results"]["technology"]["changes"][0]["from"] == "full"
    capsys.readouterr()


def test_cli_rollback_restores_previous_labels(namespaces, tmp_path):
    path = store.init_articles_db("finance")
    _legacy_label(path, "finance", "谎报全文", "只有几个字", "https://legacy.example.com/k2", "full")
    report = tmp_path / "rollback.json"
    cli_main(["relabel-completeness", "--category", "finance", "--apply", "--out", str(report)])
    assert [row["completeness"] for row in _labels(path)] == ["short"]

    assert cli_main(["relabel-completeness", "--rollback", str(report)]) == 0
    assert [row["completeness"] for row in _labels(path)] == ["full"]


def test_cli_category_all_covers_registered_categories_only(namespaces, tmp_path):
    for slug in REGISTERED:
        path = store.init_articles_db(slug)
        _legacy_label(path, slug, "谎报全文", "只有几个字", f"https://legacy.example.com/{slug}", "full")
    demo = store.init_articles_db("demo")
    _legacy_label(demo, "demo", "demo 谎报", "只有几个字", "https://legacy.example.com/demo", "full")

    report = tmp_path / "all.json"
    assert cli_main(["relabel-completeness", "--category", "all", "--apply", "--out", str(report)]) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert set(payload["results"]) == set(REGISTERED)
    for slug in REGISTERED:
        assert [row["completeness"] for row in _labels(store.articles_db_path(slug))] == ["short"]
    assert [row["completeness"] for row in _labels(demo)] == ["full"]


def test_values_are_documented():
    assert store.COMPLETENESS_VALUES == VALUES
    assert set(VALUES) == {"full", "short", "excerpt"}
