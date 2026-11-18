"""T-17:清洗/聚类/索引增量化的精确性回归。

账本实测:social 40,506 行 `ttxt clean` 47m51.9s、`ttxt analyze` 的 index 段 236.27s,
根因是每轮从零重算(clean+O(n²)聚类+整库 FTS 重建)。增量化有一条红线:
**不许为省时间偷偷跳过聚类** —— 增量结果必须与全量结果逐行、逐簇、逐索引行相等。

这里钉死八件事:
1. 增量 == 全量(簇/哈希/簇表/FTS 行集/检索命中逐条相等,头条验收);
2. 分桶聚类 == 暴力聚类(同一语料交叉核对,候选数必须小于暴力预算);
3. 无变化的重复跑:零清洗、零指纹变化、零索引写入,簇数不变;
4. 改一行只重算那一行(正文与标题分别验证);
5. FTS 增量预算阈值必须生效(超限走整库重建);
6. clean_version 升级 ⇒ 全行失效重算;
7. 管线外删文章:台账孤儿行被裁剪、FTS 跟着删,覆盖率保持相等;
8. 本任务不得给 articles 加列(只新增 pipeline_state 台账表)。
"""

from __future__ import annotations

import sqlite3

import pytest

from ttxt import config as cfg
from ttxt.analysis import rag
from ttxt.crawler import clusters, pipeline, simhash, store
from ttxt.crawler.extract import ExtractedItem


# ------------------------------------------------------------------ 语料构造

def _item(title: str, content: str, url: str) -> ExtractedItem:
    return ExtractedItem(title=title, content=content, excerpt="", author="", published_at=None, url=url)


def _distinct_body(index: int) -> str:
    return (
        f"科技观察独立报道第{index}篇:量子{index}号机与材料科学交叉进展,"
        f"实验编号{index}记录了超导比特相干时间的变化曲线与误差分析。"
    )


def _near_variants(base: str, count: int, url_tag: str) -> list[ExtractedItem]:
    """生成 base 的近重复变体:只动尾部标点;用 simhash 实测过滤,
    汉明距离 >3 的变体当场丢弃 —— 种子语料诚实,不靠"以为它是近重复"。"""
    suffixes = ["", "!", "?", "。嗯", "啦", "。啊", "呢"]
    out: list[ExtractedItem] = []
    base_hash = simhash.simhash64(base)
    used = 0
    for suffix in suffixes:
        if len(out) >= count:
            break
        variant = base + suffix
        if variant == base and used:
            continue
        if simhash.hamming(base_hash, simhash.simhash64(variant)) > 3:
            continue
        used += 1
        out.append(_item(f"近重复{url_tag}之{used}", variant, f"https://near.example.com/{url_tag}-{used}"))
    return out


def _collision_rows(count: int) -> list[ExtractedItem]:
    """标题只差数字、数字不参与分词 ⇒ 清洗文本分词完全一致 ⇒ simhash 完全相同的碰撞家族。"""
    body = "系列报道固定正文:城市更新试点在三个城区同步铺开,老旧小区改造进度每月公示一次。"
    return [
        _item(f"系列报道第{i}篇", body, f"https://collision.example.com/{i}") for i in range(1, count + 1)
    ]


def _seed_corpus_split() -> tuple[list[ExtractedItem], list[ExtractedItem]]:
    """(首批 20 行, 追加 200 行)。两库共用同一插入顺序 ⇒ id 一一对应。"""
    seed: list[ExtractedItem] = []
    seed.append(_item("空正文行", "", "https://seed.example.com/empty"))
    seed.extend(_near_variants("人工智能正在改变世界,机器学习模型需要大量数据来训练和优化。", 3, "seedA"))
    for i in range(1, 17):
        seed.append(_item(f"首批独立报道{i}", _distinct_body(i), f"https://seed.example.com/{i}"))
    # 长度比陷阱:同文重复两遍(哈希相同、比例 2 ≤ 2.5 应合并)与四遍(比例 4 > 2.5 不得合并)
    base = "短讯:本市明起降温并伴有大风黄色预警。"
    seed.append(_item("短讯本尊", base, "https://seed.example.com/ratio-a"))
    seed.append(_item("短讯双拼", base + base, "https://seed.example.com/ratio-b"))

    extra: list[ExtractedItem] = []
    for i in range(1, 9):
        extra.extend(_near_variants(_distinct_body(100 + i), 2, f"extra{i}"))
    extra.extend(_collision_rows(60))
    base2 = "快讯:港短驳运输批量上线超级电容车。"
    extra.append(_item("快讯本尊", base2, "https://extra.example.com/ratio-a"))
    extra.append(_item("快讯四拼", base2 * 4, "https://extra.example.com/ratio-c"))
    filler: list[ExtractedItem] = []
    i = 1
    while len(extra) + len(filler) < 130:
        filler.append(
            _item(
                f"追加独立报道{i}",
                f"追加独立报道正文{i}:行业{3000 + i}数据与政策解读,样本编号{i},含图表两组。",
                f"https://extra.example.com/d{i}",
            )
        )
        i += 1
    extra.extend(filler)
    # 1 空正文 + 3 近重复 + 16 独立 + 2 长度比样本 = 22
    assert len(seed) == 22
    return seed, extra


# ------------------------------------------------------------------ 快照比对

def _connect(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _articles_state(db_path) -> dict[int, tuple]:
    con = _connect(db_path)
    try:
        return {
            int(row["id"]): (row["cluster_id"], row["simhash"])
            for row in con.execute("SELECT id, cluster_id, simhash FROM articles")
        }
    finally:
        con.close()


def _cluster_rows(db_path) -> list[tuple]:
    con = _connect(db_path)
    try:
        return sorted(
            (row["cluster_id"], row["representative_id"], row["member_count"])
            for row in con.execute("SELECT cluster_id, representative_id, member_count FROM article_clusters")
        )
    finally:
        con.close()


def _fts_rowids(db_path) -> list[int]:
    con = _connect(db_path)
    try:
        return [int(row[0]) for row in con.execute("SELECT rowid FROM articles_fts ORDER BY rowid")]
    finally:
        con.close()


def _article_ids(db_path) -> list[int]:
    con = _connect(db_path)
    try:
        return [int(row[0]) for row in con.execute("SELECT id FROM articles ORDER BY id")]
    finally:
        con.close()


def _ledger_fps(db_path) -> dict[int, str]:
    con = _connect(db_path)
    try:
        return {
            int(row["article_id"]): row["content_fp"]
            for row in con.execute("SELECT article_id, content_fp FROM pipeline_state")
        }
    finally:
        con.close()


# ------------------------------------------------------------------ 1. 增量 == 全量(头条验收)


def test_incremental_equals_full_exact(tmp_path):
    db_a = store.init_articles_db("demo", db_path=tmp_path / "a.db")
    db_b = store.init_articles_db("demo", db_path=tmp_path / "b.db")
    seed, extra = _seed_corpus_split()

    store.insert_items("demo", "t", seed, db_path=db_a)
    stats_cold = pipeline.run_pipeline("demo", db_path=db_a)
    assert stats_cold["mode"] == "full", f"首轮台账为空,必须如实报 full:{stats_cold}"

    # 追加 ~200 行(近重复/长度比陷阱/碰撞家族/独立行/短行混合),再删一行种子行
    store.insert_items("demo", "t", extra, db_path=db_a)
    con = _connect(db_a)
    try:
        victim = con.execute("SELECT id FROM articles WHERE url_normalized LIKE '%ratio-a%' ORDER BY id LIMIT 1").fetchone()
        deleted_id = int(victim["id"])
        con.execute("DELETE FROM articles WHERE id = ?", (deleted_id,))
        con.commit()
    finally:
        con.close()
    stats_inc = pipeline.run_pipeline("demo", db_path=db_a)
    assert stats_inc["mode"] == "incremental", f"第二轮必须走增量:{stats_inc}"
    assert stats_inc["index"]["mode"] == "delta", f"索引应走 delta:{stats_inc['index']}"
    assert stats_inc["fingerprints_pruned"] == 1
    assert stats_inc["index"]["deleted"] == 1

    # 对照库 B:同一最终行集一次到位,full=True 全量路径
    store.insert_items("demo", "t", seed, db_path=db_b)
    store.insert_items("demo", "t", extra, db_path=db_b)
    con = _connect(db_b)
    try:
        victim = con.execute("SELECT id FROM articles WHERE url_normalized LIKE '%ratio-a%' ORDER BY id LIMIT 1").fetchone()
        con.execute("DELETE FROM articles WHERE id = ?", (int(victim["id"]),))
        con.commit()
    finally:
        con.close()
    stats_full = pipeline.run_pipeline("demo", db_path=db_b, full=True)
    assert stats_full["mode"] == "full"

    # 行集一致 ⇒ 才能逐 id 比对
    assert _article_ids(db_a) == _article_ids(db_b)

    # 逐行:cluster_id + simhash 完全相等
    assert _articles_state(db_a) == _articles_state(db_b)
    # 簇表:cluster_id/representative_id/member_count 逐行相等
    assert _cluster_rows(db_a) == _cluster_rows(db_b)
    # 回执三计数相等
    for key in ("articles", "clusters", "near_duplicate_pairs", "singletons"):
        assert stats_inc[key] == stats_full[key], f"{key}: 增量 {stats_inc[key]} != 全量 {stats_full[key]}"
    # 簇结构自检:近重复家族与碰撞家族真的并进了簇(不是把聚类跳过了换出来的"相等")
    assert any(count >= 3 for *_rest, count in _cluster_rows(db_a)), "近重复/碰撞家族必须成簇"
    assert any(count == 60 for *_rest, count in _cluster_rows(db_a)), "simhash 完全相同的碰撞家族必须并成一簇"

    # FTS 行集相等 + 检索命中相等
    assert _fts_rowids(db_a) == _fts_rowids(db_b)
    assert len(_fts_rowids(db_a)) == len(_article_ids(db_a)), "articles_fts 行数必须等于 articles 行数"
    for query in ("人工智能", "量子", "系列报道", "超级电容"):
        hits_a = [hit["article_id"] for hit in rag.search("demo", query, limit=10, db_path=db_a)]
        hits_b = [hit["article_id"] for hit in rag.search("demo", query, limit=10, db_path=db_b)]
        assert hits_a == hits_b, f"查询 {query!r} 检索命中不一致:{hits_a} vs {hits_b}"
    assert rag.search("demo", "系列报道", limit=5, db_path=db_a), "检索必须真的有结果"


# ------------------------------------------------------------------ 2. 分桶 == 暴力


def test_bucketed_matches_bruteforce_on_same_corpus(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "c.db")
    seed, extra = _seed_corpus_split()
    store.insert_items("demo", "t", seed, db_path=db_path)
    store.insert_items("demo", "t", extra, db_path=db_path)
    store.insert_items(
        "demo",
        "t",
        [_item(f"碰撞扩展{i}", "固定碰撞正文:本地生活服务业复苏指数连续三个月环比上行,商户流水样本采集自两千家门店。", f"https://collision2.example.com/{i}") for i in range(1, 101)],
        db_path=db_path,
    )
    total = len(_article_ids(db_path))
    assert total >= 250

    pipeline.run_pipeline("demo", db_path=db_path)

    stats_bucketed = clusters.cluster_category(
        "demo", db_path=db_path, method="bucketed", clean_version=pipeline.CLEAN_VERSION
    )
    state_bucketed = _articles_state(db_path)
    clusters_bucketed = _cluster_rows(db_path)

    stats_brute = clusters.cluster_category("demo", db_path=db_path, method="brute")
    state_brute = _articles_state(db_path)
    clusters_brute = _cluster_rows(db_path)

    for key in ("articles", "clusters", "near_duplicate_pairs", "singletons"):
        assert stats_bucketed[key] == stats_brute[key], f"{key}: 分桶 {stats_bucketed[key]} != 暴力 {stats_brute[key]}"
    assert state_bucketed == state_brute, "逐行 cluster_id/simhash 必须与暴力路径逐条相等"
    assert clusters_bucketed == clusters_brute

    budget = total * (total - 1) // 2
    assert stats_bucketed["brute_force_pair_budget"] == budget
    assert stats_bucketed["candidate_pairs_examined"] < budget, (
        f"分桶候选 {stats_bucketed['candidate_pairs_examined']} 必须少于暴力预算 {budget}"
    )
    assert stats_brute["candidate_pairs_examined"] == budget
    # 台账复用:管线跑过之后分桶聚类不重算哈希
    assert stats_bucketed["hashes_reused"] == total
    # 幂等写回:结果与库内一致时不再放大写 UPDATE
    assert stats_bucketed["writeback_updates"] == 0


# ------------------------------------------------------------------ 3. 无变化重跑 = 空转


def test_idempotent_noop_second_run(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "d.db")
    seed, extra = _seed_corpus_split()
    store.insert_items("demo", "t", seed, db_path=db_path)
    store.insert_items("demo", "t", extra, db_path=db_path)
    first = pipeline.run_pipeline("demo", db_path=db_path)
    state_first = _articles_state(db_path)

    second = pipeline.run_pipeline("demo", db_path=db_path)
    assert second["mode"] == "incremental"
    assert second["rows_cleaned"] == 0
    assert second["cleaned"] == 0
    assert second["fingerprints_changed"] == 0
    assert second["fingerprints_pruned"] == 0
    assert second["rows_reused"] == second["rows_seen"] == len(_article_ids(db_path))
    index = second["index"]
    assert index["mode"] == "delta" and index["indexed"] == 0 and index["deleted"] == 0
    assert index["delta_ops_since_full"] == 0
    for key in ("clusters", "near_duplicate_pairs", "singletons"):
        assert second[key] == first[key], f"{key}: 空转轮簇计数漂了:{first[key]} -> {second[key]}"
    assert _articles_state(db_path) == state_first


# ------------------------------------------------------------------ 4. 改一行只重算一行


def test_content_change_invalidates_exactly_one_row(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "e.db")
    items = [_item(f"独立报道{i}", _distinct_body(i), f"https://mut.example.com/{i}") for i in range(1, 9)]
    store.insert_items("demo", "t", items, db_path=db_path)
    pipeline.run_pipeline("demo", db_path=db_path)
    fps_before = _ledger_fps(db_path)
    state_before = _articles_state(db_path)

    con = _connect(db_path)
    try:
        target = con.execute("SELECT id FROM articles ORDER BY id LIMIT 1 OFFSET 3").fetchone()["id"]
        con.execute("UPDATE articles SET content = ? WHERE id = ?", (_distinct_body(999999), target))
        con.commit()
    finally:
        con.close()

    stats = pipeline.run_pipeline("demo", db_path=db_path)
    assert stats["rows_cleaned"] == 1
    assert stats["fingerprints_changed"] == 1
    assert stats["rows_reused"] == len(_article_ids(db_path)) - 1
    assert stats["index"]["mode"] == "delta"
    assert stats["index"]["indexed"] == 1 and stats["index"]["deleted"] == 0
    fps_after = _ledger_fps(db_path)
    assert fps_after[int(target)] != fps_before[int(target)], "被改行的指纹必须刷新"
    others = {aid for aid in fps_after if aid != int(target)}
    assert all(fps_after[aid] == fps_before[aid] for aid in others), "其余行指纹不得动"
    state_after = _articles_state(db_path)
    changed_ids = {aid for aid in state_after if state_after[aid] != state_before.get(aid)}
    assert int(target) in changed_ids, "被改行的 simhash/簇归属必须重算"


def test_title_change_invalidates_exactly_one_row(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "f.db")
    items = [_item(f"独立标题{i}", _distinct_body(i), f"https://mut2.example.com/{i}") for i in range(1, 7)]
    store.insert_items("demo", "t", items, db_path=db_path)
    pipeline.run_pipeline("demo", db_path=db_path)
    fps_before = _ledger_fps(db_path)

    con = _connect(db_path)
    try:
        target = con.execute("SELECT id FROM articles ORDER BY id LIMIT 1 OFFSET 2").fetchone()["id"]
        con.execute("UPDATE articles SET title = ? WHERE id = ?", ("换了一个新标题", target))
        con.commit()
    finally:
        con.close()

    stats = pipeline.run_pipeline("demo", db_path=db_path)
    assert stats["rows_cleaned"] == 1
    assert stats["index"]["mode"] == "delta" and stats["index"]["indexed"] == 1
    fps_after = _ledger_fps(db_path)
    assert fps_after[int(target)] != fps_before[int(target)], "标题参与指纹,改标题必须失效"
    assert all(
        fps_after[aid] == fps_before[aid] for aid in fps_after if aid != int(target)
    ), "只有这一行失效"


# ------------------------------------------------------------------ 5. FTS 回退阈值


def test_changed_rows_over_threshold_forces_full(tmp_path, monkeypatch):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "g.db")
    items = [_item(f"阈值报道{i}", _distinct_body(i), f"https://th.example.com/{i}") for i in range(1, 11)]
    store.insert_items("demo", "t", items, db_path=db_path)
    pipeline.run_pipeline("demo", db_path=db_path)

    monkeypatch.setattr(
        cfg, "pipeline_settings", lambda: {**cfg.PIPELINE_DEFAULTS, "fts_delta_full_rebuild_at_changed_rows": 4}
    )
    con = _connect(db_path)
    try:
        rows = con.execute("SELECT id FROM articles ORDER BY id LIMIT 5").fetchall()
        for offset, row in enumerate(rows):
            con.execute("UPDATE articles SET content = ? WHERE id = ?", (_distinct_body(700000 + offset), row["id"]))
        con.commit()
    finally:
        con.close()

    stats = pipeline.run_pipeline("demo", db_path=db_path)
    assert stats["index"]["mode"] == "full", f"5 行变更 ≥ 阈值 4,必须整库重建:{stats['index']}"
    assert stats["index"]["delta_ops_since_full"] == 0


def test_cumulative_delta_ops_over_budget_forces_full(tmp_path, monkeypatch):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "h.db")
    items = [_item(f"额度报道{i}", _distinct_body(i), f"https://budget.example.com/{i}") for i in range(1, 9)]
    store.insert_items("demo", "t", items, db_path=db_path)
    pipeline.run_pipeline("demo", db_path=db_path)

    monkeypatch.setattr(
        cfg, "pipeline_settings", lambda: {**cfg.PIPELINE_DEFAULTS, "fts_delta_ops_budget": 3}
    )
    con = _connect(db_path)
    modes: list[str] = []
    try:
        ids = [int(row["id"]) for row in con.execute("SELECT id FROM articles ORDER BY id")]
        for round_index, article_id in enumerate(ids[:4], start=1):
            con.execute("UPDATE articles SET content = ? WHERE id = ?", (_distinct_body(800000 + round_index), article_id))
            con.commit()
            stats = pipeline.run_pipeline("demo", db_path=db_path)
            modes.append(stats["index"]["mode"])
    finally:
        con.close()
    # 每轮 1 行变更:累计墓碑 1,2,3 ⇒ 第三轮 2+1 ≥ 预算 3 必须触发整库重建
    assert modes[:2] == ["delta", "delta"], f"预算内应走增量:{modes}"
    assert modes[2] == "full", f"累计增删达到 fts_delta_ops_budget 必须整库重建:{modes}"


# ------------------------------------------------------------------ 6. clean_version 升级 = 全行失效


def test_clean_version_bump_invalidates_every_row(tmp_path, monkeypatch):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "i.db")
    items = [_item(f"版本报道{i}", _distinct_body(i), f"https://ver.example.com/{i}") for i in range(1, 11)]
    store.insert_items("demo", "t", items, db_path=db_path)
    pipeline.run_pipeline("demo", db_path=db_path)
    fps_before = _ledger_fps(db_path)

    monkeypatch.setattr(pipeline, "CLEAN_VERSION", "p42-bumped-v2")
    stats = pipeline.run_pipeline("demo", db_path=db_path)
    assert stats["clean_version"] == "p42-bumped-v2"
    assert stats["rows_cleaned"] == len(_article_ids(db_path)), "清洗规则升级 ⇒ 每一行都必须重算"
    assert stats["fingerprints_changed"] == len(fps_before)
    fps_after = _ledger_fps(db_path)
    assert all(fps_after[aid] != fps_before[aid] for aid in fps_before)
    assert stats["rows_reused"] == 0


# ------------------------------------------------------------------ 7. 台账孤儿行裁剪


def test_orphan_ledger_rows_pruned_and_fts_follows(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "j.db")
    items = [_item(f"删除演练{i}", _distinct_body(i), f"https://orph.example.com/{i}") for i in range(1, 8)]
    store.insert_items("demo", "t", items, db_path=db_path)
    pipeline.run_pipeline("demo", db_path=db_path)
    assert len(_fts_rowids(db_path)) == 7

    con = _connect(db_path)
    try:
        doomed = [int(row["id"]) for row in con.execute("SELECT id FROM articles ORDER BY id LIMIT 2")]
        # 模拟 dedupe-history / legacy 侧的管线外删行:只删 articles,不管台账与索引
        con.executemany("DELETE FROM articles WHERE id = ?", [(aid,) for aid in doomed])
        con.commit()
    finally:
        con.close()

    stats = pipeline.run_pipeline("demo", db_path=db_path)
    assert stats["fingerprints_pruned"] == 2
    assert stats["index"]["mode"] == "delta" and stats["index"]["deleted"] == 2
    assert len(_fts_rowids(db_path)) == len(_article_ids(db_path)) == 5, (
        "doctor.db.fts_coverage 按 articles_fts 行数 == articles 行数对账,删行后必须追平"
    )
    ledger = set(_ledger_fps(db_path))
    assert ledger == set(_article_ids(db_path)), "台账孤儿行必须被裁剪"


# ------------------------------------------------------------------ 8. 加性 schema 守卫


def test_migrate_adds_no_columns_to_articles(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "k.db")
    with _connect(db_path) as con:
        base_columns = [row[1] for row in con.execute("PRAGMA table_info(articles)")]

    store.migrate_articles_db("demo", db_path=db_path)
    with _connect(db_path) as con:
        after_first = [row[1] for row in con.execute("PRAGMA table_info(articles)")]
    store.migrate_articles_db("demo", db_path=db_path)
    with _connect(db_path) as con:
        after_second = [row[1] for row in con.execute("PRAGMA table_info(articles)")]
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    p3_columns = [name for name, _ddl in store.P3_COLUMNS]
    assert after_second == after_first, "重复迁移不得再改 articles 结构"
    assert after_first == base_columns + p3_columns, (
        "T-17 只允许新增台账表,不许给 articles 加列;列集必须 = 基础列 + 既有 P3 列"
    )
    assert {"pipeline_state", "pipeline_state_meta"} <= tables
    # simhash 是既有 P3 列(articles 合法列),不在泄漏守卫集合里
    leaked = {"content_fp", "text_len", "fp_at"} & set(after_second)
    assert not leaked, f"台账列泄漏进 articles:{leaked}"
