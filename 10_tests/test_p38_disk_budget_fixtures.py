"""P38 磁盘止血回归:FTS 墓碑、裁剪副本字节预算、scratch 回收、WAL 边车治理。

这些测试的立场是"证明已经修好",不是"证明能跑":
- FTS 重建若退回 ``DELETE FROM``,第二次重建的索引体积会翻倍 ⇒ 红。
- 夹具副本若退回整库逐字节复制,字节预算与裁剪语义断言 ⇒ 红。
- ``-wal`` 边车若不再受 ``journal_size_limit`` 约束,housekeep/doctor 的读数 ⇒ 红。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from ttxt import config as cfg
from ttxt import db as ttxt_db
from ttxt import doctor, housekeep
from ttxt.analysis import rag
from ttxt.crawler import store

# 测试基座在根 conftest 里以私有模块名加载(initial conftest 钩子),``import conftest``
# 拿到的是哪一个不确定;这里显式取同一份模块,monkeypatch 才会命中夹具真正使用的全局。
_HARNESS = sys.modules.get("_ttxt_harness_conftest")
if _HARNESS is None or not hasattr(_HARNESS, "build_trimmed_category_db"):
    _spec = importlib.util.spec_from_file_location(
        "_ttxt_harness_conftest", Path(__file__).with_name("conftest.py")
    )
    assert _spec is not None and _spec.loader is not None
    _HARNESS = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_HARNESS)


# --------------------------------------------------------------------------- 建库脚手架


def _fts_payload_bytes(db_path: Path) -> int:
    """FTS5 影子表 ``_data`` 的实际负载:墓碑会不会留下,只有这里看得见。"""
    con = sqlite3.connect(db_path)
    try:
        return int(con.execute("SELECT COALESCE(SUM(LENGTH(block)), 0) FROM articles_fts_data").fetchone()[0])
    finally:
        con.close()


def _seed_articles(db_path: Path, slug: str, count: int) -> None:
    path = store.init_articles_db(slug, db_path=db_path)
    store.migrate_articles_db(slug, db_path=path)
    con = ttxt_db.connect(path)
    try:
        for i in range(1, count + 1):
            con.execute(
                "INSERT INTO articles(category, source, title, content, cleaned_content, url,"
                " url_normalized, fetched_at, content_hash, published_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    slug,
                    "seed",
                    f"人工智能观察笔记 第 {i} 篇",
                    f"人工智能 与 开源 效率 工具 结合 的 第 {i} 篇 长文 内容。" * 60,
                    f"人工智能 与 开源 效率 工具 结合 的 第 {i} 篇 清洗 内容。" * 60,
                    f"https://example.com/{slug}/{i}",
                    f"example.com/{slug}/{i}",
                    "2026-01-01T00:00:00",
                    f"hash-{slug}-{i}",
                    "2026-01-01T00:00:00",
                ),
            )
        con.commit()
    finally:
        con.close()


def _rows(db_path: Path, sql: str) -> list:
    con = sqlite3.connect(db_path)
    try:
        return [row[0] for row in con.execute(sql)]
    finally:
        con.close()


# ------------------------------------------------------------- FTS 重建不留死段(根因 A)


def test_fts_rebuild_twice_does_not_double_the_index(tmp_path):
    """第二次整库重建不得把旧段留在库里(实测 social 因此虚占 811 MB)。"""
    db_path = tmp_path / "technology.db"
    _seed_articles(db_path, "technology", 40)
    rag.rebuild_index("technology", db_path=db_path)
    first = _fts_payload_bytes(db_path)
    first_file = db_path.stat().st_size
    assert first > 0

    again = rag.rebuild_index("technology", db_path=db_path)
    second = _fts_payload_bytes(db_path)
    assert again["indexed"] == 40
    assert second <= first * 1.2, (
        f"FTS 索引从 {first:,} 字节涨到 {second:,} 字节:重建又在写墓碑。"
        "必须 DROP + CREATE(见 ttxt/analysis/rag.py:rebuild_index)。"
    )
    assert db_path.stat().st_size <= first_file * 1.2


def test_fts_rebuild_keeps_search_usable(tmp_path):
    """DROP + CREATE 之后索引必须照常可查,否则省盘是靠拆功能换来的。"""
    db_path = tmp_path / "social.db"
    _seed_articles(db_path, "social", 12)
    rag.rebuild_index("social", db_path=db_path)
    rag.rebuild_index("social", db_path=db_path)
    hits = rag.search("social", "人工智能", limit=5, db_path=db_path)
    assert len(hits) == 5
    assert {hit["article_id"] for hit in hits} <= set(_rows(db_path, "SELECT id FROM articles"))


def test_rag_reuses_single_source_of_truth_for_fts_ddl():
    """重建必须复刻 store 里的同一份 FTS DDL,否则重建出来的索引和爬取时建的不是一回事。"""
    assert "articles_fts" in store.ARTICLES_FTS_DDL
    assert store.ARTICLES_FTS_DDL in store.V3_SCHEMA


# ----------------------------------------------------------- 裁剪副本(根因 B:每测整库)


@pytest.fixture()
def fake_production_category(tmp_path, monkeypatch):
    """造一份"生产类目库":整簇、派生行、标定值都齐,再按 rows=5 裁剪。"""
    cats = tmp_path / "01_categories" / "seedslug"
    cats.mkdir(parents=True)
    source = cats / "articles.db"
    path = store.init_articles_db("seedslug", db_path=source)
    con = ttxt_db.connect(path)
    try:
        store.migrate_articles_db("seedslug", db_path=path)
        plan = [
            # (id, published_at, cluster_id)
            (1, "2026-01-01T00:00:00", "C1"),
            (2, "2026-01-02T00:00:00", "C1"),
            (3, "2026-01-03T00:00:00", "C1"),
            (4, "2026-11-10T00:00:00", "C1"),  # 新:把整簇 C1 拽回来
            (5, "2026-11-11T00:00:00", "C2"),
            (6, "2026-11-12T00:00:00", "C2"),
            (7, "2026-11-13T00:00:00", "C2"),
            (8, "2025-12-30T00:00:00", "C3"),  # 老且孤立 ⇒ 应被裁掉
            (9, "2025-12-31T00:00:00", "C3"),
        ]
        for aid, published, cluster in plan:
            con.execute(
                "INSERT INTO articles(id, category, source, title, content, cleaned_content, url,"
                " url_normalized, fetched_at, content_hash, published_at, cluster_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    aid,
                    "seedslug",
                    "seed",
                    f"人工智能观察笔记 第 {aid} 篇",
                    f"人工智能 内容 第 {aid} 篇。" * 80,
                    f"人工智能 清洗 内容 第 {aid} 篇。" * 80,
                    f"https://example.com/a/{aid}",
                    f"example.com/a/{aid}",
                    "2026-11-01T00:00:00",
                    f"hash-{aid}",
                    published,
                    cluster,
                ),
            )
            con.execute(
                "INSERT INTO article_metrics(article_id, reads, data_source, captured_at)"
                " VALUES(?,?,?,?)",
                (aid, aid * 100, "seed", "2026-11-01T00:00:00"),
            )
            con.execute(
                "INSERT INTO metric_snapshots(article_id, captured_at, reads) VALUES(?,?,?)",
                (aid, "2026-11-01T00:00:00", aid * 100),
            )
            con.execute(
                "INSERT INTO viral_labels(article_id, label, confidence_tier, labeled_at)"
                " VALUES(?,?,?,?)",
                (aid, "viral", "A", "2026-11-01T00:00:00"),
            )
            con.execute(
                "INSERT INTO evidence_links(draft_id, claim, article_id, created_at) VALUES(?,?,?,?)",
                (f"d{aid}", "人工智能", aid, "2026-11-01T00:00:00"),
            )
        for cluster, members in (("C1", 4), ("C2", 3), ("C3", 2)):
            con.execute(
                "INSERT INTO article_clusters(cluster_id, category, representative_id, member_count,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (cluster, "seedslug", 1, members, "2026-11-01T00:00:00", "2026-11-01T00:00:00"),
            )
        con.execute(
            "INSERT INTO articles_meta(key, value) VALUES('viral_calibration', ?)",
            (json.dumps({"reads_p90": 120000, "sample": 37}),),
        )
        con.commit()
    finally:
        con.close()
    rag.rebuild_index("seedslug", db_path=source)

    monkeypatch.setattr(_HARNESS, "TRIM_DIR", tmp_path / "trim")
    return source


def test_trim_copy_keeps_whole_clusters_and_drops_orphan_children(fake_production_category):
    source = fake_production_category
    record = _HARNESS.build_trimmed_category_db(source, rows=5)
    cache = Path(record["cache"])

    assert record["slug"] == "seedslug"
    assert record["rows_source"] == 9
    assert record["rows_kept"] == 7, "最近 5 篇 + 整簇 C1 补齐 ⇒ 1..7 必须都在"
    assert _rows(cache, "SELECT id FROM articles ORDER BY id") == [1, 2, 3, 4, 5, 6, 7]

    con = sqlite3.connect(cache)
    try:
        for table, column in (
            ("article_metrics", "article_id"),
            ("metric_snapshots", "article_id"),
            ("viral_labels", "article_id"),
        ):
            ids = {row[0] for row in con.execute(f"SELECT {column} FROM {table}")}
            assert ids == set(range(1, 8)), f"{table} 留下孤儿派生行或误删在库行:{sorted(ids)}"
        linked = {row[0] for row in con.execute("SELECT article_id FROM evidence_links WHERE article_id IS NOT NULL")}
        assert linked == set(range(1, 8))
        clusters = {row[0] for row in con.execute("SELECT cluster_id FROM article_clusters")}
        assert clusters == {"C1", "C2"}, "整簇保留、全裁的簇必须一并清掉"
        calibration = con.execute(
            "SELECT value FROM articles_meta WHERE key='viral_calibration'"
        ).fetchone()
        assert calibration is not None, "裁剪把 viral_calibration 裁掉,面板会假报未标定"
    finally:
        con.close()

    assert rag.search("seedslug", "人工智能", limit=3, db_path=cache), "裁剪后 FTS 必须仍可查"
    assert cache.stat().st_size < source.stat().st_size


def test_trim_copy_never_touches_the_production_directory(fake_production_category):
    """生产库只被逐字节读取:出现 ``-wal/-shm`` 就是违反测试红线。"""
    source = fake_production_category
    _HARNESS.build_trimmed_category_db(source, rows=4)
    sidecars = sorted(p.name for p in source.parent.glob("articles.db-*"))
    assert sidecars == [], f"生产类目目录出现边车:{sidecars}"


def test_trim_cache_is_reused_and_invalidated_when_source_changes(fake_production_category):
    source = fake_production_category
    first = _HARNESS.build_trimmed_category_db(source, rows=5)
    again = _HARNESS.build_trimmed_category_db(source, rows=5)
    assert again["cache"] == first["cache"]
    assert again["built_at"] == first["built_at"], "同一份生产库必须复用缓存,不得每次重建"

    stamp = source.stat().st_atime_ns, source.stat().st_mtime_ns
    os.utime(source, ns=(stamp[0], stamp[1] + 1_000_000))
    try:
        refreshed = _HARNESS.build_trimmed_category_db(source, rows=5)
    finally:
        os.utime(source, ns=stamp)
    assert refreshed["cache"] != first["cache"], "生产库一变,缓存键必须失效"
    assert Path(refreshed["cache"]).exists()

    lineage = Path(first["cache"] + ".lineage.json")
    assert lineage.exists()
    on_disk = json.loads(lineage.read_text(encoding="utf-8"))
    assert on_disk["source_sha256"] == refreshed["source_sha256"] or on_disk["cache"] == refreshed["cache"]


def test_trim_copy_respects_the_byte_budget(fake_production_category, tmp_path):
    record = _HARNESS.build_trimmed_category_db(fake_production_category, rows=5)
    assert record["cache_bytes"] <= _HARNESS.TEST_COPY_MAX_MB * 1024 * 1024


# ------------------------------------------------------------------- scratch 运行目录回收


@pytest.fixture()
def scratch_tree(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    scratch = root / "07_tmp"
    basetemp = scratch / housekeep.BASETEMP_DIRNAME
    for name in ("pytest-1", "pytest-2", "pytest-3", "pytest-4", "loose-orphan"):
        target = basetemp / name
        target.mkdir(parents=True)
        (target / "payload.bin").write_bytes(b"x" * 4096)
    for name in ("pytest_trim", "reclaim-20260101T000000"):
        keep = basetemp / name
        keep.mkdir(parents=True)
        (keep / "keep.json").write_text("{}", encoding="utf-8")
    outside = root / "01_categories" / "social"
    outside.mkdir(parents=True)
    (outside / "articles.db").write_bytes(b"y" * 4096)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", root)
    monkeypatch.setattr(cfg, "SCRATCH_DIR", scratch)
    return root, scratch, basetemp


def test_only_scratch_paths_are_reclaimable(scratch_tree):
    _root, scratch, basetemp = scratch_tree
    assert housekeep._inside_scratch(scratch / "x")
    assert not housekeep._inside_scratch(scratch.parent / "01_categories")
    assert not housekeep._inside_scratch(scratch.parent)


def test_reclaim_scratch_dry_run_deletes_nothing(scratch_tree):
    _root, _scratch, basetemp = scratch_tree
    out = housekeep.reclaim_scratch_runs(dry_run=True, keep_runs=1, live_grace_minutes=0.0)
    assert out["dry_run"] is True
    assert out["would_delete_count"] == 4
    assert out["deleted_count"] == 0
    assert out["candidates_bytes"] >= 4 * 4096
    assert out["receipt"] is None, "看账不许造回执"
    assert all((basetemp / name).exists() for name in ("pytest-1", "pytest-2", "pytest-3", "loose-orphan"))


def test_reclaim_scratch_apply_frees_bytes_and_writes_ledger(scratch_tree):
    _root, scratch, basetemp = scratch_tree
    out = housekeep.reclaim_scratch_runs(dry_run=False, keep_runs=1, live_grace_minutes=0.0)
    assert out["deleted_count"] == 4
    assert out["freed_bytes"] >= 4 * 4096
    assert [item["run"] for item in out["kept_runs"]] == ["pytest-4"], "只留最新一轮运行目录"
    assert not (basetemp / "pytest-1").exists()
    assert (basetemp / "pytest-4").exists()
    for name in ("pytest_trim", "reclaim-20260101T000000"):
        assert (basetemp / name).exists(), f"{name} 是取证件/缓存,回收不得动它"
    assert (scratch.parent / "01_categories" / "social" / "articles.db").exists(), (
        "07_tmp 之外的路径绝不允许被回收删除"
    )
    assert out["receipt"], "实删必须留回执"
    ledger = Path(out["receipt"])
    assert housekeep._inside_scratch(ledger)
    doc = json.loads(ledger.read_text(encoding="utf-8"))
    assert doc["dry_run"] is False and doc["freed_bytes"] >= 4 * 4096


def test_reclaim_scratch_respects_live_grace(scratch_tree):
    _root, _scratch, basetemp = scratch_tree
    out = housekeep.reclaim_scratch_runs(dry_run=True, keep_runs=0, live_grace_minutes=30.0)
    assert out["would_delete_count"] == 0
    assert len(out["skipped_live"]) == 5


# ----------------------------------------------------------------------- WAL 边车治理


@pytest.fixture()
def wal_scene(tmp_path, monkeypatch):
    """一个正在写 WAL 的类目库 + 一个不存在的 system.db 路径。"""
    cats = tmp_path / "01_categories"
    (cats / "social").mkdir(parents=True)
    db_path = cats / "social" / "articles.db"
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", cats)
    monkeypatch.setattr(ttxt_db, "CONTROL_DIR", tmp_path / "00_control")
    con = ttxt_db.connect(db_path)
    try:
        con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, body TEXT)")
        con.executemany("INSERT INTO t(body) VALUES(?)", [("z" * 2000,) for _ in range(500)])
        con.commit()
        yield db_path, con
    finally:
        con.close()


def test_db_connect_caps_wal_retention(tmp_path):
    con = ttxt_db.connect(tmp_path / "cap.db")
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert con.execute("PRAGMA journal_size_limit").fetchone()[0] == ttxt_db.WAL_SIZE_LIMIT_BYTES
    finally:
        con.close()


def test_wal_cap_is_not_looser_than_the_connection_limit():
    """配置只能把上限调紧,不能调松:否则改一行 config 就把 826 MB 现场放回来。"""
    configured = int(cfg.load_config()["disk"]["wal_max_mb"]) * 1024 * 1024
    assert configured <= ttxt_db.WAL_SIZE_LIMIT_BYTES
    assert housekeep._wal_threshold_bytes(1024) == 1024


def test_checkpoint_db_wals_merges_sidecar_without_losing_rows(wal_scene):
    db_path, con = wal_scene
    wal = db_path.with_name(db_path.name + "-wal")
    assert wal.exists() and wal.stat().st_size > 1024

    survey = housekeep.db_wal_warnings(min_wal_bytes=1024)
    assert [item["path"] for item in survey] == [str(wal)]

    dry = housekeep.checkpoint_db_wals(dry_run=True, min_wal_bytes=1024)
    assert dry["candidates"] == 1 and dry["results"][0]["action"] == "would_checkpoint"
    assert wal.stat().st_size > 1024, "干跑不得动盘"

    out = housekeep.checkpoint_db_wals(min_wal_bytes=1024)
    assert out["candidates"] == 1
    record = out["results"][0]
    assert record["action"] == "checkpointed", record
    assert record["wal_bytes_after"] == 0
    assert out["wal_bytes_reclaimed"] >= 1024
    assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 500, "合并只搬页,不许丢行"
    assert housekeep.db_wal_warnings(min_wal_bytes=1024) == []


def test_checkpoint_skips_small_idle_wals(wal_scene):
    """低于阈值的空闲边车不动:否则每次巡检都要去重排生产库。"""
    db_path = wal_scene[0]
    wal = db_path.with_name(db_path.name + "-wal")
    big = housekeep.db_wal_warnings(min_wal_bytes=1)
    assert len(big) == 1
    huge = housekeep.db_wal_warnings(min_wal_bytes=wal.stat().st_size + 1_000_000)
    assert huge == []


# --------------------------------------------------------------------- doctor 必须看得见


def test_doctor_fails_on_oversized_scratch_and_wal(isolated_data_root, monkeypatch, capsys):
    patched = json.loads(json.dumps(cfg.load_config()))
    patched.setdefault("disk", {})["scratch_max_gb"] = 0.0
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: patched)
    monkeypatch.setattr(
        housekeep,
        "db_wal_warnings",
        lambda min_wal_bytes=None: [
            {
                "path": "01_categories/social/articles.db-wal",
                "wal_bytes": 826_224_832,
                "db_bytes": 1_777_143_808,
            }
        ],
    )
    assert doctor.run_doctor() == 1
    out = capsys.readouterr().out
    scratch_line = next(line for line in out.splitlines() if "disk.scratch" in line)
    wal_line = next(line for line in out.splitlines() if "db.wal_sidecars" in line)
    assert scratch_line.startswith("[FAIL]")
    assert "--reclaim-apply" in scratch_line, "FAIL 必须带上能执行的命令"
    assert wal_line.startswith("[FAIL]")
    assert "--checkpoint-wals" in wal_line
    assert "788MB" in wal_line


def test_housekeep_run_can_dry_run_the_wal_checkpoint(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    monkeypatch.setattr(cfg, "PROJECT_ROOT", root)
    monkeypatch.setattr(cfg, "SCRATCH_DIR", root / "07_tmp")
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", root / "01_categories")
    monkeypatch.setattr(ttxt_db, "CONTROL_DIR", root / "00_control")
    out = housekeep.run(checkpoint_wals=True, checkpoint_wals_dry_run=True)
    assert out["checkpoint_db_wals"] == {
        "dry_run": True,
        "candidates": 0,
        "wal_bytes_reclaimed": 0,
        "results": [],
    }
    assert "wal_warnings" in out["report"]["caps_status"]
