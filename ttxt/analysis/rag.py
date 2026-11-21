"""类目 RAG:SQLite FTS5 + 中文双字词索引(免外部分词服务)。

T-17(实测 analyze 的 index 段 236.27s = 整库 DROP+CREATE 重建):新增
``sync_index`` 做行id 对齐(rowid = article_id)的增量更新,并按预算强制回到
整库重建:

- FTS5 的 DELETE 只写墓碑,旧段 VACUUM 也收不回(规则 61 的教训,social 索引
  实测 811 MB 虚占)⇒ 累计增删操作数超过 ``pipeline.fts_delta_ops_budget``
  或单轮变更达到 ``pipeline.fts_delta_full_rebuild_at_changed_rows``,必须整库重建
  (重建走 ``rebuild_index`` 的 DROP+CREATE,不是 ``DELETE FROM``)。
- 台账没登记 rowid 对齐、台账没覆盖每一行文章(外部写方塞过行)、配置关掉增量、
  ``--full`` 点名 ⇒ 一律走整库重建,绝不半截。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from ttxt import config as cfg
from ttxt.crawler.simhash import tokenize
from ttxt.db import connect

INDEX_CHAR_LIMIT = 8000

# 主题相关度(R64-1,2026-11-21 实测):BM25 只在候选池内**相对**归一,没有绝对下限;
# ``search`` 的 OR 回退还会把只沾一个双字词的稿件塞进来。10 题探针里 4 题
# (邻里纠纷/高空抛物/装修 噪音/催婚)的 5 个素材槽被离题文章占满
# (例:「装修 噪音」选中 外卖副业/棋牌室/KTV 加盟/旧衣服回收/北漂租房),
# 模型拿着不相干的材料必产出编造 ⇒ 先给"是否真在讲这个主题"一个可复核的尺子。
TOPIC_TOKEN_LIMIT = 12  # 与 search() 取词口径完全一致(同一分词器、同一上限)
TITLE_HITS_MIN = 1      # 标题命中主题词
PER_1K_WITH_TITLE = 2.0  # 标题点题时,正文密度下限(每千字命中次数)
PER_1K_ONLY = 8.0        # 标题没点题时,只有正文密集复述主题词才算数


def bigrams(text: str) -> str:
    return " ".join(tokenize(text or ""))


def _db_path(category: str, db_path: Path | None) -> Path:
    return Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"


def _body_of(row) -> str:
    return bigrams((row["cleaned_content"] or row["content"] or "")[:INDEX_CHAR_LIMIT])


def _tables_of(con) -> set[str]:
    return {str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _meta_get(con, key: str) -> str | None:
    row = con.execute("SELECT value FROM pipeline_state_meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def _meta_set(con, key: str, value: str) -> None:
    con.execute("INSERT OR REPLACE INTO pipeline_state_meta(key, value) VALUES(?, ?)", (key, value))


def rebuild_index(category: str, db_path: Path | None = None) -> dict[str, int]:
    """整库重建 FTS(强制回退通道;`ttxt analyze --full` 与预算超限都走这里)。

    重建用 ``DROP + CREATE`` 而不是 ``DELETE FROM``:FTS5 的 DELETE 只写墓碑,旧段
    (2026-11-17 实测 social 整库索引 811 MB)永远留在 b-tree 里,VACUUM 也拿不回来。
    手动事务(pysqlite 对 DDL 不开隐式事务)保证中途崩溃整体回滚,不会留下半截索引。

    T-17:插入显式携带 ``rowid = articles.id``,并在台账盖 ``fts_rowid_aligned`` 章 ——
    ``sync_index`` 的 O(log n) 增量删除以此为前提;同时把增删墓碑计数清零。
    """
    from ttxt.crawler.store import ARTICLES_FTS_DDL, PIPELINE_STATE_SCHEMA

    target = _db_path(category, db_path)
    con = connect(target)
    con.isolation_level = None
    try:
        # 台账表可能在未迁移的老库/裁剪副本上不存在(如 conftest 直接在副本上重建索引):
        # 幂等补建,不新增 articles 列,也不读任何生产库以外的约定。
        con.executescript(PIPELINE_STATE_SCHEMA)
        rows = con.execute("SELECT id, title, cleaned_content, content FROM articles ORDER BY id").fetchall()
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute("DROP TABLE IF EXISTS articles_fts")
            con.execute(ARTICLES_FTS_DDL)
            con.executemany(
                "INSERT INTO articles_fts(rowid, article_id, title, body) VALUES (?, ?, ?, ?)",
                [(row["id"], row["id"], bigrams(row["title"] or ""), _body_of(row)) for row in rows],
            )
        except Exception:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
        # 盖章必须排在 COMMIT 之后:重建失败时不能谎称索引已行id对齐。
        _meta_set(con, "fts_rowid_aligned", "1")
        _meta_set(con, "last_full_index_at", cfg.now_iso())
        _meta_set(con, "fts_delta_ops_since_full", "0")
        con.commit()
    finally:
        con.close()
    return {"indexed": len(rows)}


def sync_index(
    category: str,
    db_path: Path | None = None,
    changed_ids: list[int] | None = None,
    removed_ids: list[int] | None = None,
    force_full: bool = False,
) -> dict:
    """增量更新检索索引;前提不成立就整库重建(绝不半截)。

    增量前提(全部满足才走 delta):
    1. 台账章 ``fts_rowid_aligned == '1'``(本轮之前的一次整库重建确实按 rowid=id 写入);
    2. ``articles_fts`` 与 ``pipeline_state`` 都在;
    3. 台账对每一行文章都有指纹 —— 缺行说明有外部写方(如 legacy migrate)绕过管线
       塞过文章,该整库对账一次;
    4. 预算:单轮 ``changed+removed`` 与累计墓碑数都没超 ``pipeline`` 配置阈值;
    5. 配置 ``delta_enabled`` 开着,调用方没点名 ``force_full``。

    changed/removed 没显式传入时,由台账 vs articles 推导(缺指纹=changed,
    台账有行文章没了=removed),保证外部写方的改动照样被覆盖。
    """
    started = time.perf_counter()
    target = _db_path(category, db_path)
    settings = cfg.pipeline_settings()
    at_changed = int(settings.get("fts_delta_full_rebuild_at_changed_rows", 2000))
    ops_budget = int(settings.get("fts_delta_ops_budget", 20000))

    con = connect(target)
    try:
        tables = _tables_of(con)
        if "pipeline_state" not in tables or "articles_fts" not in tables:
            changed, removed, ops, ops_since = [], [], 0, 0
            aligned, unledgered = None, 1
        else:
            aligned = _meta_get(con, "fts_rowid_aligned")
            ops_since = int(_meta_get(con, "fts_delta_ops_since_full") or 0)
            unledgered = con.execute(
                "SELECT COUNT(*) FROM articles a LEFT JOIN pipeline_state p ON p.article_id = a.id"
                " WHERE p.article_id IS NULL"
            ).fetchone()[0]
            if changed_ids is None:
                changed = [
                    int(row[0])
                    for row in con.execute(
                        "SELECT a.id FROM articles a LEFT JOIN pipeline_state p ON p.article_id = a.id"
                        " WHERE p.article_id IS NULL ORDER BY a.id"
                    )
                ]
            else:
                changed = list(changed_ids)
            if removed_ids is None:
                removed = [
                    int(row[0])
                    for row in con.execute(
                        "SELECT p.article_id FROM pipeline_state p LEFT JOIN articles a ON a.id = p.article_id"
                        " WHERE a.id IS NULL ORDER BY p.article_id"
                    )
                ]
            else:
                removed = list(removed_ids)
            ops = len(changed) + len(removed)
        use_delta = (
            not force_full
            and bool(settings.get("delta_enabled", True))
            and aligned == "1"
            and unledgered == 0
            and ops < at_changed
            and ops_since + ops < ops_budget
        )
        if not use_delta:
            deleted_full = (
                int(con.execute("SELECT COUNT(*) FROM articles_fts").fetchone()[0])
                if "articles_fts" in tables
                else 0
            )
            candidate_count = len(changed) + len(removed) if aligned is not None else 0
            con.close()
            con = None
            full = rebuild_index(category, db_path=target)
            return {
                "mode": "full",
                "indexed": full["indexed"],
                "deleted": deleted_full,
                "candidate_count": candidate_count,
                "delta_ops_since_full": 0,
                "elapsed_s": round(time.perf_counter() - started, 3),
            }

        # 半截索引不可留:增删写进同一个写事务,异常整体回滚。
        con.isolation_level = None
        con.execute("BEGIN IMMEDIATE")
        inserted = deleted = 0
        try:
            for article_id in removed:
                deleted += con.execute("DELETE FROM articles_fts WHERE rowid = ?", (article_id,)).rowcount
            for article_id in changed:
                # 先删后插:行可能已在索引里(内容变更),FTS5 重复 rowid 插入会把旧内容留在段里。
                con.execute("DELETE FROM articles_fts WHERE rowid = ?", (article_id,))
                row = con.execute(
                    "SELECT title, cleaned_content, content FROM articles WHERE id = ?", (article_id,)
                ).fetchone()
                if row is None:
                    continue
                con.execute(
                    "INSERT INTO articles_fts(rowid, article_id, title, body) VALUES (?, ?, ?, ?)",
                    (article_id, article_id, bigrams(row["title"] or ""), _body_of(row)),
                )
                inserted += 1
        except Exception:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
        ops_now = ops_since + ops
        _meta_set(con, "fts_delta_ops_since_full", str(ops_now))
        con.commit()
    finally:
        if con is not None:
            con.close()
    return {
        "mode": "delta",
        "indexed": inserted,
        "deleted": deleted,
        "candidate_count": len(changed) + len(removed),
        "delta_ops_since_full": ops_since + ops,
        "elapsed_s": round(time.perf_counter() - started, 3),
    }


def search(category: str, query: str, limit: int = 10, db_path: Path | None = None) -> list[dict]:
    tokens = list(dict.fromkeys(tokenize(query or "")))[:12]
    if not tokens:
        return []
    target = _db_path(category, db_path)
    con = connect(target)

    def run(operator: str) -> list[dict]:
        match = f" {operator} ".join(f'"{token}"' for token in tokens)
        try:
            rows = con.execute(
                """
                SELECT a.id AS article_id, a.title, a.url, a.published_at, s.rank
                FROM (
                  SELECT article_id, bm25(articles_fts) AS rank
                  FROM articles_fts
                  WHERE articles_fts MATCH ?
                  ORDER BY rank
                  LIMIT ?
                ) s
                JOIN articles a ON a.id = s.article_id
                ORDER BY s.rank
                """,
                (match, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception:  # noqa: BLE001 - 空索引/非法查询返回空结果
            return []

    try:
        results = run("AND")
        # 短查询保持严格(避免双字词误报);较长查询才允许 OR 回退保召回
        if not results and len(tokens) > 3:
            results = run("OR")
        return results
    finally:
        con.close()


def topic_tokens(query: str) -> list[str]:
    """主题取词:与 ``search`` 同一分词器、同一去重与 12 词上限。"""
    return list(dict.fromkeys(tokenize(query or "")))[:TOPIC_TOKEN_LIMIT]


def topic_relevance(query: str, title: str, body: str) -> dict:
    """判定一篇文章是否**真的在讲这个主题**(确定性、可复核、零模型调用)。

    尺子只用命中次数与密度,取词与 FTS 完全一致(双字词):

    - ``title_hits>=1 且 per_1k>=PER_1K_WITH_TITLE``:标题点题且正文确实在讲它;
    - 或 ``per_1k>=PER_1K_ONLY``:标题没写主题但正文密集复述主题词(生产语料里
      转述稿/栏目稿常见);
    - 取词为空(纯英文/符号主题)⇒ 无尺子可用,如实放行并写明理由,**不假装过滤**。

    命中用非重叠 ``findall`` ⇒ 数值偏保守(宁可少算,不误留离题稿)。
    阈值来自 2026-11-21 的 10 题生产快照探针(``07_tmp/r64_material_probe.json``):
    该式把 6 个健康主题(7—15 篇相关)与 4 个漂移主题(0—1 篇相关)干净分开,无中间地带。
    """
    tokens = topic_tokens(query)
    body_len = len(body or "")
    if not tokens:
        return {
            "tokens": 0, "title_hits": 0, "body_hits": 0, "coverage": 0.0, "per_1k": 0.0,
            "body_len": body_len, "relevant": True, "rule": "主题无可提取词 ⇒ 不做相关度过滤",
        }
    pattern = re.compile(
        "|".join(re.escape(token) for token in sorted(set(tokens), key=len, reverse=True))
    )
    title_hits = pattern.findall(title or "")
    body_hits = pattern.findall(body or "")
    total_tokens = len(set(tokens))
    distinct = len({*title_hits, *body_hits})
    per_1k = len(body_hits) / max(1.0, body_len / 1000.0)
    by_title = len(title_hits) >= TITLE_HITS_MIN and per_1k >= PER_1K_WITH_TITLE
    by_density = per_1k >= PER_1K_ONLY
    return {
        "tokens": total_tokens,
        "title_hits": len(title_hits),
        "body_hits": len(body_hits),
        "coverage": round(distinct / total_tokens, 3),
        "per_1k": round(per_1k, 2),
        "body_len": body_len,
        "relevant": bool(by_title or by_density),
        "rule": (
            f"title_hits>={TITLE_HITS_MIN} 且 per_1k>={PER_1K_WITH_TITLE}"
            f",或 per_1k>={PER_1K_ONLY}"
        ),
    }

