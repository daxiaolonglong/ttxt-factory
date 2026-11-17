"""P37 旧资产迁移(账本 T-14 / P26 唯一主线)的红名单测试。

口径全部来自 `08_wiki/reports/P25-two-new-channels-and-fts-index-20261116.md` 第九节的六条硬前置,
以及 2026-11-17 对 `资产库/01_语料库/factory.db` 的只读实测:

- 53,678 篇文章、URL 全是 `https://www.toutiao.com/article/<gid>/`;
- 真实计数来自 search_ssr:`read_count>0` **33,708** 行(P25 把 19,970/33,708 写反了,以实测为准)、
  `digg>0` 18,505、`comment>0` 8,716、`collect>0` 20,133;
- `article_bodies` 190,555 行,正文最长 8,000 字(旧采集的**截断上限**,不是原文长度);
- 类目只有两个标签:`primary_type = 真人故事 / 赚钱案例`。

因此本文件钉死六件事:①未映射类目必须拒绝并计数;②计数 0 必须按缺失(NULL)入库;
③迁移必须幂等;④旧库必须只读且前后 sha256 一字不差;⑤`collect_count` 语义未证实,
绝不允许改名成 `favorites`(规则 41);⑥类目映射只能来自配置,不许写死在代码里。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from ttxt.analysis import metrics_import, rag
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem
from ttxt.legacy import factory, migrate

LEGACY_SCHEMA = """
CREATE TABLE articles(
  gid TEXT PRIMARY KEY, title TEXT, keyword TEXT, profile_id TEXT, task_id TEXT,
  publish_time TEXT, primary_type TEXT, tier_v2 TEXT, training_quality_v2 REAL,
  read_count INTEGER, digg_count INTEGER, is_elite INTEGER, url TEXT, synced_at TEXT,
  is_viral INTEGER DEFAULT 0, comment_count INTEGER DEFAULT 0,
  collect_count INTEGER DEFAULT 0, metrics_source TEXT, hot_score REAL, hot_level TEXT);
CREATE TABLE article_bodies(
  gid TEXT PRIMARY KEY, title TEXT, author TEXT, abstract TEXT, body TEXT,
  primary_type TEXT, ai_related INTEGER, signals TEXT, synced_at TEXT);
"""


def _legacy_db(path) -> str:
    """按旧库真实列建一份小库(测试即 schema 契约:列名一改就会红)。"""
    con = sqlite3.connect(path)
    con.executescript(LEGACY_SCHEMA)
    rows = [
        # 1) 赚钱案例 → finance:计数齐全,正文够长
        #    实测坑:旧库 `publish_time` 是 **Unix 时间戳字符串**,不是日期串(2026-11-17 抽样取证)。
        ("1001", "月薪三千到三万:小城市做AI副业的三个月", "AI副业", "1791649800", "赚钱案例",
         5200, 210, 34, 88, 3,
         "县城做AI副业的第三个月,我把主业的工资翻了一倍。方法不复杂,关键是选对工具。" * 20),
        # 2) 真人故事 → social:read_count=0 ⇒ 必须按缺失处理,不能当 0(digg 有真实计数)
        ("1002", "48岁被裁员后,我在菜市场找到了新出路", "再就业", "1791738000", "真人故事",
         0, 7, 0, 0, 3,
         "他在工厂干了二十年,厂区关门那天他没有哭。真正难的是第二天早上六点。" * 20),
        # 3) 未映射 primary_type ⇒ 必须拒绝并计数
        ("1003", "老屋的瓦片", None, "1791828000", "情感美文",
         900, 40, 5, 12, 3,
         "老屋的瓦片上长了草,祖母说那是雨写的字。" * 20),
        # 4) 正文太短 ⇒ 拒绝并计数(不得当全文语料)
        ("1004", "AI岗位月薪翻三倍", "AI岗位", "1791918000", "赚钱案例",
         1200, 60, 8, 20, 3, "只有摘要级的两行字。"),
        # 5) 与现网已有 URL 相同 ⇒ 按身份去重,不得新增行
        ("1005", "已经存在于现网的一篇文章", "效率工具", "1792008000", "赚钱案例",
         800, 30, 4, 9, 3,
         "这篇文章的 URL 与现网库里那一行完全相同,迁移时必须按 URL 身份判重。" * 20),
        # 6) 与 1001 同标题同正文、不同 gid/URL ⇒ 旧采集的"一稿多发",按 content_hash 判重
        ("1006", "月薪三千到三万:小城市做AI副业的三个月", "AI副业转载", "1791563400", "赚钱案例",
         4100, 90, 12, 5, 3,
         "县城做AI副业的第三个月,我把主业的工资翻了一倍。方法不复杂,关键是选对工具。" * 20),
        # 7) 三列计数全为 0 ⇒ 整行没有可用观测:文章仍是语料,但不得伪造一条空观测
        ("1007", "六十三岁学跳舞,她第一次站上舞台", "退休生活", "1791649700", "真人故事",
         0, 0, 0, 0, 3,
         "她把广场舞的每一个动作都录下来,回家对着屏幕反复看。三个月后,她站上了区里的舞台。" * 20),
    ]
    con.executemany(
        "INSERT INTO articles(gid,title,keyword,publish_time,primary_type,read_count,digg_count,"
        "comment_count,collect_count,tier_v2,url,metrics_source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (gid, title, kw, pub, ptype, reads, digg, comment, collect, tier,
             f"https://www.toutiao.com/article/{gid}/",
             '{"read":"search_ssr","interaction":"search_ssr","followers":"info_api"}')
            for (gid, title, kw, pub, ptype, reads, digg, comment, collect, tier, _body) in rows
        ],
    )
    con.executemany(
        "INSERT INTO article_bodies(gid,title,author,abstract,body,primary_type) VALUES(?,?,?,?,?,?)",
        [(gid, title, f"作者{gid}", "", body, ptype)
         for (gid, title, _kw, _pub, ptype, *_rest, body) in rows],
    )
    con.commit()
    con.close()
    return str(path)


@pytest.fixture()
def legacy_db(tmp_path):
    return _legacy_db(tmp_path / "factory.db")


@pytest.fixture()
def policy_file(tmp_path):
    path = tmp_path / "legacy_migration.json"
    path.write_text(
        json.dumps(
            {
                "source_id": "legacy_factory_search_ssr",
                "category_map": {"赚钱案例": "finance", "真人故事": "social"},
                "min_body_chars": 200,
                "zero_as_missing_fields": ["reads", "likes", "comments"],
                "max_rows_per_category": 100,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _db_for(tmp_path):
    """只解析路径、绝不建库:建库是迁移自己的事(apply 才建,预演不建)。"""

    def resolver(category: str) -> str:
        return str(tmp_path / f"{category}.db")

    return resolver


def _init_db(tmp_path, category: str) -> str:
    return str(store.migrate_articles_db(category, db_path=tmp_path / f"{category}.db"))


def _rows(db_path: str, table: str) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(f"SELECT * FROM {table}")]
    finally:
        con.close()


def test_unmapped_primary_type_is_rejected_and_counted(legacy_db, policy_file, tmp_path):
    """未映射的 primary_type 必须拒绝入库并当场计数(P26 硬前置 2),不许"就近塞进某个类目"。"""
    policy = migrate.load_policy(policy_file)
    receipt = migrate.run(
        policy, legacy_db=legacy_db, apply=True, batch_size=2, db_path_for=_db_for(tmp_path)
    )
    assert receipt["rejected"]["unmapped_category"] == 1
    for slug in ("finance", "social"):
        titles = [r["title"] for r in _rows(str(tmp_path / f"{slug}.db"), "articles")]
        assert "老屋的瓦片" not in titles


def test_zero_counts_stay_null_not_zero(legacy_db, policy_file, tmp_path):
    """`read_count=0` 是"没取到计数"而不是"没人看"(P25 §9),必须 NULL 入库。

    反向口径同样钉死:三列全为 0 的旧行(1007)整行没有可用观测 ⇒ 文章照样是语料,
    但不许生成一条全 NULL 的"观测记录",否则下游会把"没有观测"读成"观测过但没数据"。
    """
    policy = migrate.load_policy(policy_file)
    migrate.run(policy, legacy_db=legacy_db, apply=True, batch_size=2, db_path_for=_db_for(tmp_path))
    metrics = _rows(str(tmp_path / "social.db"), "article_metrics")
    assert len(metrics) == 1
    assert metrics[0]["reads"] is None
    assert metrics[0]["comments"] is None
    assert metrics[0]["likes"] == 7
    # 缺失不等于 0:这行不得被算成"低质文章"
    assert metrics[0]["data_source"] == "legacy_factory_search_ssr"
    titles = [r["title"] for r in _rows(str(tmp_path / "social.db"), "articles")]
    assert "六十三岁学跳舞,她第一次站上舞台" in titles


def test_collect_count_never_becomes_favorites(legacy_db, policy_file, tmp_path):
    """旧库 `collect_count` 语义未证实 ⇒ 绝不改名冒充收藏(规则 41 / P25 §十二.3)。"""
    policy = migrate.load_policy(policy_file)
    migrate.run(policy, legacy_db=legacy_db, apply=True, batch_size=2, db_path_for=_db_for(tmp_path))
    for slug in ("finance", "social"):
        for row in _rows(str(tmp_path / f"{slug}.db"), "article_metrics"):
            assert row["favorites"] is None
        # 展现量(showCount)旧库根本没有 ⇒ 也不许凭空造
        for row in _rows(str(tmp_path / f"{slug}.db"), "article_metrics"):
            assert row["impressions"] is None


def test_migration_is_idempotent(legacy_db, policy_file, tmp_path):
    """同一批旧资产跑第二次不得新增行、不得新增快照(幂等)。"""
    resolver = _db_for(tmp_path)
    policy = migrate.load_policy(policy_file)
    first = migrate.run(policy, legacy_db=legacy_db, apply=True, batch_size=2, db_path_for=resolver)
    counts_first = {s: len(_rows(str(tmp_path / f"{s}.db"), "articles")) for s in ("finance", "social")}
    snap_first = {s: len(_rows(str(tmp_path / f"{s}.db"), "metric_snapshots")) for s in ("finance", "social")}
    second = migrate.run(policy, legacy_db=legacy_db, apply=True, batch_size=2, db_path_for=resolver)
    counts_second = {s: len(_rows(str(tmp_path / f"{s}.db"), "articles")) for s in ("finance", "social")}
    snap_second = {s: len(_rows(str(tmp_path / f"{s}.db"), "metric_snapshots")) for s in ("finance", "social")}

    assert counts_second == counts_first
    assert snap_second == snap_first
    assert second["inserted"] == 0
    assert first["inserted"] == 4  # 1001/1002/1005/1007;1003 未映射、1004 正文过短、1006 一稿多发
    assert second["duplicates"] >= 4


def test_legacy_database_is_opened_read_only_and_untouched(legacy_db, policy_file, tmp_path):
    """旧库是只读资产:迁移前后 sha256 必须一字不差(P26 硬前置 6 / 规则 33)。"""
    before = factory.sha256_file(legacy_db)
    policy = migrate.load_policy(policy_file)
    migrate.run(policy, legacy_db=legacy_db, apply=True, batch_size=2, db_path_for=_db_for(tmp_path))
    assert factory.sha256_file(legacy_db) == before
    assert migrate.sha256_of_legacy_db(policy, legacy_db) == before
    con = factory.open_read_only(legacy_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM articles")
    finally:
        con.close()


def test_url_identity_collision_does_not_add_row(legacy_db, policy_file, tmp_path):
    """现网已有同一 URL ⇒ 迁移按文章身份判重,不得新增行,且必须计入 duplicates。"""
    resolver = _db_for(tmp_path)
    db = _init_db(tmp_path, "finance")
    store.insert_items(
        "finance",
        "toutiao_tech",
        [
            ExtractedItem(
                title="已经存在于现网的一篇文章",
                content="现网里这篇文章的正文与旧库不同,但 URL 身份相同。",
                excerpt="",
                author="现网作者",
                published_at="2026-10-15T12:00:00",
                url="https://www.toutiao.com/article/1005/",
            )
        ],
        db_path=db,
    )
    policy = migrate.load_policy(policy_file)
    receipt = migrate.run(policy, legacy_db=legacy_db, apply=True, batch_size=2, db_path_for=resolver)
    finance_titles = [r["title"] for r in _rows(db, "articles")]
    assert finance_titles.count("已经存在于现网的一篇文章") == 1
    assert receipt["duplicates"] >= 1


def test_category_map_comes_from_config_not_code(tmp_path):
    """映射写在 `00_control/legacy_migration.json` 里;换配置就得换路由(P26 硬前置 2)。"""
    src = tmp_path / "factory2.db"
    _legacy_db(src)
    cfg = tmp_path / "policy2.json"
    cfg.write_text(
        json.dumps(
            {
                "source_id": "legacy_factory_search_ssr",
                "category_map": {"赚钱案例": "technology", "真人故事": "finance"},
                "min_body_chars": 200,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    resolver = _db_for(tmp_path)
    receipt = migrate.run(
        migrate.load_policy(cfg), legacy_db=str(src), apply=True, batch_size=2, db_path_for=resolver
    )
    # 换配置就换路由:赚钱案例 → technology、真人故事 → finance(与默认配置完全反过来)
    assert {r["title"] for r in _rows(str(tmp_path / "technology.db"), "articles")} == {
        "月薪三千到三万:小城市做AI副业的三个月",
        "已经存在于现网的一篇文章",
    }
    assert {r["title"] for r in _rows(str(tmp_path / "finance.db"), "articles")} == {
        "48岁被裁员后,我在菜市场找到了新出路",
        "六十三岁学跳舞,她第一次站上舞台",
    }
    assert receipt["rejected"]["unmapped_category"] == 1
    assert not (tmp_path / "social.db").exists()


def test_short_body_is_rejected_and_counted(legacy_db, policy_file, tmp_path):
    """短正文(旧采集只有摘要)不得当全文语料混进来,且要计数可见。"""
    policy = migrate.load_policy(policy_file)
    receipt = migrate.run(
        policy, legacy_db=legacy_db, apply=True, batch_size=2, db_path_for=_db_for(tmp_path)
    )
    assert receipt["rejected"]["body_too_short"] == 1
    for slug in ("finance", "social"):
        titles = [r["title"] for r in _rows(str(tmp_path / f"{slug}.db"), "articles")]
        assert "AI岗位月薪翻三倍" not in titles


def test_dry_run_reports_without_writing_a_single_byte(legacy_db, policy_file, tmp_path):
    """硬前置 3:先做只读碰撞预演,报"会新增/会撞/会拒"多少,不许顺手写库。"""
    resolver = _db_for(tmp_path)
    touched = _init_db(tmp_path, "finance")
    con = sqlite3.connect(touched)
    con.execute("INSERT INTO articles_meta(key,value) VALUES('sentinel','占位')")
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    before = hashlib.sha256((tmp_path / "finance.db").read_bytes()).hexdigest()
    policy = migrate.load_policy(policy_file)
    report = migrate.run(policy, legacy_db=legacy_db, apply=False, batch_size=2, db_path_for=resolver)

    assert hashlib.sha256((tmp_path / "finance.db").read_bytes()).hexdigest() == before
    assert not (tmp_path / "social.db").exists()  # 预演绝不新建命名空间
    assert report["would_insert"] == 4  # 1005 + 1001 + 1002 + 1007
    assert report["would_collide_within_migration"] == 1  # 1006 与 1001 一稿多发
    assert report["would_collide_same_namespace"] == 0
    assert report["rejected"]["unmapped_category"] == 1
    assert report["inserted"] == 0


def test_migrated_corpus_is_searchable_without_analyze(legacy_db, policy_file, tmp_path):
    """P25 教训(采集漏建 FTS ⇒ 当天语料搜不到)在迁移路径上同样成立:导入当场进索引。"""
    receipt = migrate.run(
        migrate.load_policy(policy_file),
        legacy_db=legacy_db,
        apply=True,
        batch_size=2,
        db_path_for=_db_for(tmp_path),
    )
    assert receipt["index_errors"] == []
    assert receipt["indexed"] == 4
    hits = rag.search("finance", "副业", db_path=str(tmp_path / "finance.db"))
    assert hits and hits[0]["title"].startswith("月薪三千")


def test_legacy_source_must_be_registered_in_config_whitelist():
    """生产配置里必须登记这个来源,否则 `metrics_import` 会整批拒绝(白名单是硬闸)。"""
    allow = metrics_import.load_authorized_sources()
    assert "legacy_factory_search_ssr" in allow
    assert metrics_import.credibility_rank(allow["legacy_factory_search_ssr"]) > 0


def test_epoch_publish_time_becomes_local_iso(legacy_db, policy_file, tmp_path):
    """旧库 `publish_time` 是 Unix 秒(实测抽样),必须归一化成带时区 ISO,不许留原始串。

    留原样会让 `published_at` 上的新鲜度过滤、按发布时间排序全部失效(索引存在但比较的是字符串)。
    """
    from datetime import datetime

    migrate.run(
        migrate.load_policy(policy_file),
        legacy_db=legacy_db,
        apply=True,
        batch_size=2,
        db_path_for=_db_for(tmp_path),
    )
    expected = datetime.fromtimestamp(1791649800).astimezone().isoformat(timespec="seconds")
    rows = [r for r in _rows(str(tmp_path / "finance.db"), "articles") if r["title"].startswith("月薪三千")]
    assert len(rows) == 1
    assert rows[0]["published_at"] == expected


def test_receipt_counts_bodies_at_legacy_truncation_cap(legacy_db, policy_file, tmp_path):
    """旧采集把正文截在 8,000 字 ⇒ 回执必须数出"有多少行是截断的",下游才知道语料边界。"""
    receipt = migrate.run(
        migrate.load_policy(policy_file),
        legacy_db=legacy_db,
        apply=True,
        batch_size=2,
        db_path_for=_db_for(tmp_path),
        body_cap=640,  # 测试语料故意短,把上限压到 640 才能数到
    )
    assert receipt["body_at_legacy_cap"] >= 1
