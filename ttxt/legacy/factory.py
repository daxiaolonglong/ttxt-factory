"""旧资产(``资产库/01_语料库/factory.db``)的只读访问层(账本 T-14 / P26 主线)。

三条硬口径,全部来自 P25 §九 与 2026-11-17 的只读实测:

- ``资产库/`` 一律只读:只以 ``mode=ro`` 打开,迁移前后 sha256 必须一字不差(规则 33/43)。
- 只读库上做排序 / GROUP BY 会把临时 B-tree 落盘,而本机 TMP 不可写 ⇒
  ``unable to open database file``(P25 §十四 实测坑)。这里每个连接都下
  ``PRAGMA temp_store=MEMORY``,和 ``ttxt/db.py:102`` 同一做法。
- 计数语义:``read_count = 0`` 是**没取到计数**,不是"没人看"(53,678 行里 19,970 行为 0),
  必须按缺失(NULL)入库;``collect_count`` 语义未证实 ⇒ 永不改名成 ``favorites``(规则 41)。
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from ttxt.crawler.clean import normalize_text
from ttxt.crawler.cleaner import clean_article
from ttxt.crawler.extract import ExtractedItem
from ttxt.crawler import timeparse

# 旧库列 → 新系统指标列。只映射语义已证实的三列;展现量(showCount)旧库根本没有。
LEGACY_METRIC_MAP = {"read_count": "reads", "digg_count": "likes", "comment_count": "comments"}
# 旧库里有、但语义未证实的列:只作证据保留,绝不改名入库(规则 41 的同一纪律)。
UNPROVEN_LEGACY_COLUMNS = ("collect_count", "hot_score", "hot_level", "tier_v2", "is_elite", "is_viral")

ARTICLE_SELECT = """
SELECT a.gid, a.title, a.keyword, a.publish_time, a.primary_type, a.profile_id,
       a.read_count, a.digg_count, a.comment_count, a.collect_count,
       a.url, a.metrics_source, a.synced_at,
       b.author, b.abstract, b.body,
       LENGTH(b.body) AS body_len
FROM articles a
LEFT JOIN article_bodies b ON b.gid = a.gid
ORDER BY a.publish_time DESC, a.gid
"""


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_read_only(path: Path | str) -> sqlite3.Connection:
    """只读打开旧库。写操作会当场报错,这是刻意的(规则 33:语料资产不许被迁移改写)。"""
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def article_count(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])


def iter_articles(con: sqlite3.Connection, *, limit: int | None = None) -> Iterator[dict]:
    """流式产出旧库文章(旧库 53,678 行、正文平均 2,208 字,一次性读进内存会吃掉几百 MB)。"""
    sql = ARTICLE_SELECT + (" LIMIT ?" if limit else "")
    cursor = con.execute(sql, (limit,) if limit else ())
    while True:
        row = cursor.fetchone()
        if row is None:
            break
        yield {key: row[key] for key in row.keys()}


def to_item(record: dict, *, min_body_chars: int) -> tuple[ExtractedItem | None, str | None]:
    """旧库一行 → 采集侧的 ``ExtractedItem``,让迁移走 ``store.insert_items`` 的同一条去重/入库路径。

    返回 ``(None, 拒绝原因)`` 时原因必须可计数,不许静默丢弃(规则 47 同一纪律)。
    """
    title = normalize_text(str(record.get("title") or ""))
    if not title:
        return None, "no_title"
    body = str(record.get("body") or "")
    if not body.strip():
        return None, "no_body"
    content = clean_article(body)
    if len(content.strip()) < min_body_chars:
        return None, "body_too_short"
    url = normalize_url_or_none(record.get("url"))
    if url is None:
        return None, "bad_url"
    published_at = timeparse.normalize_datetime(str(record.get("publish_time") or ""))
    return (
        ExtractedItem(
            title=title,
            content=content,
            excerpt=content[:160].replace("\n", " "),
            author=normalize_text(str(record.get("author") or "")) or None,
            published_at=published_at,
            url=url,
            source_item_id=str(record.get("gid") or "") or None,
        ),
        None,
    )


def normalize_url_or_none(url: object) -> str | None:
    from ttxt.crawler.clean import normalize_url

    text = str(url or "").strip()
    if not text:
        return None
    try:
        return normalize_url(text)
    except ValueError:
        return None


def metrics_row(record: dict, *, zero_as_missing: tuple[str, ...]) -> dict | None:
    """旧库计数 → 新系统指标行。0 只在配置指定的字段上按缺失处理,缺失绝不折算 0。

    三个映射字段全为空 ⇒ 返回 None(这一行根本没有可用指标,不该生成一条空指标记录,
    否则下游会把"没有观测"误读成"有一篇观测过但没数据的文章")。
    """
    row: dict[str, object] = {"url": normalize_url_or_none(record.get("url"))}
    filled = 0
    for legacy_col, field in LEGACY_METRIC_MAP.items():
        raw = record.get(legacy_col)
        if raw is None:
            row[field] = None
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            row[field] = None
            continue
        if value == 0 and field in zero_as_missing:
            row[field] = None
            continue
        row[field] = value
        if value > 0:
            filled += 1
    if filled == 0:
        return None
    return row
