"""C-7:历史遗留"同一篇文章存了两行"的整改(默认干跑,逐行账本,可回滚)。

实测根因(``07_tmp/p16_dedupe_20261115/census.json``,只读取证):

- ``technology``:同一 URL ``toutiao.com/article/7486434702922531379/`` 三行,标题完全相同,
  正文 1967/1389/2787 字 —— 同一篇文章被三次渲染抽取,质量不同,是真重复。
- ``finance``:同一 URL 两行(同一篇新闻被更新过),11155/11852 字。
- ``demo`` ↔ ``technology``:13 行同 URL 同 ``content_hash`` 的真拷贝(P12 来源转正遗留)。
- ``demo``:30 行共用 ``https://quotes.toscrape.com/``,但标题正文各不相同 —— 那是列表页
  抽取器把来源页地址赋给每条(``extract.py``),``insert_items`` 已把来源页地址排除在 URL
  身份之外。**这 30 行不是重复,删了就毁掉真实语料**,所以判重必须带两道闸:
  来源页地址不算文章身份 + 标题与正文都不同就不算同一篇。

判定原则(与 ``store.insert_items`` 的写入侧口径一致,不能自创一套):

1. 同类目库内同一规范化 URL = 同一篇文章身份,但来源页/订阅地址不是文章身份。
2. 只有同一篇文章才删:同 URL 组内标题相同或 ``content_hash`` 相同。
3. 保留最完整的一行(正文最长 → ``full`` → 最小 id),语料是给写作用的。
4. 删行必须同时处理引用,绝不留下指向已删 id 的孤儿标签/指标/证据链。
5. 跨命名空间按 ``content_hash`` 认定同一篇:注册类目有所有权,未注册命名空间(``demo``)
   的拷贝可删;两个注册类目都持有 → 只报 ``ambiguous``,归属是人的决定。
6. 默认干跑;``apply`` 才写;写入全程一个 ``BEGIN IMMEDIATE`` 事务,逐行 CAS,
   行被改过就整笔回滚(和 C-1 ``relabel_completeness`` 同一套做法)。

跨库引用一律丢弃并记账,绝不搬移:类目库物理隔离(``08_wiki/db_schema.md`` §3),
一个库的行不能指向另一个库的 id。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ttxt.db import connect
from ttxt.logging import get_logger

from . import sources, store

LOG = get_logger("crawler.dedupe")

# 主键就是 article_id 的引用表:一个 id 只能有一行,所以"保留行自己没有"才搬移,
# 否则保留行已经有自己的分析结果,重复行那份只能丢弃(并记账)。
PK_REFERENCE_TABLES = ("viral_labels", "article_metrics")
# 明细型引用表(自有主键):历史数据不得丢,一律改指向保留行。
ROW_REFERENCE_TABLES = ("metric_snapshots", "evidence_links")
FTS_TABLE = "articles_fts"

# 账本条目里除文章列之外的辅助键;回滚插回 ``articles`` 时必须排除。
LEDGER_EXTRA_KEYS = frozenset({"moved_references", "dropped_references", "fts_rows", "db"})


class DedupeConflict(RuntimeError):
    """整改历史重复行时,行在计划之后被改动过,或引用状态与账本不符。

    抛出即整笔回滚:绝不删一半,也绝不覆盖别人的改动。
    """


# ------------------------------------------------------------------ 只读判定


def load_page_urls() -> list[Any]:
    """来源配置(含已停用来源):这些地址是列表页/订阅页,不是文章身份。

    与 ``insert_items(page_url=...)`` 同一口径:来源自己的地址绝不能作为文章 URL 参与判重,
    否则列表页抽出来的每一条都会被误判成彼此的重复(``extract.py`` 就是给每条赋了来源页地址)。
    """
    return sources.load_sources()


def page_urls() -> set[str]:
    urls: set[str] = set()
    try:
        entries = load_page_urls()
    except Exception as exc:  # noqa: BLE001 - 来源配置读不出来时宁可少删
        LOG.warn("dedupe.page_urls_unavailable", error=f"{type(exc).__name__}: {exc}")
        return urls
    for entry in entries:
        url = getattr(entry, "url", None)
        if not url:
            continue
        try:
            urls.add(store.normalize_url(url))
        except Exception:  # noqa: BLE001 - 单个来源地址解析失败不得影响其它来源
            continue
    return urls


def _identity(row: dict[str, Any]) -> str:
    return str(row.get("url_normalized") or row.get("url") or "").strip()


def _usable_length(row: dict[str, Any]) -> int:
    text_length = row.get("text_length")
    if isinstance(text_length, int) and text_length > 0:
        return text_length
    return len(str(row.get("cleaned_content") or row.get("content") or "").strip())


def _keeper_id(rows: list[dict[str, Any]]) -> int:
    """保留哪一行:正文最完整 → 标 full → id 最小。语料是给写作用的,先保全文。"""
    ordered = sorted(
        rows,
        key=lambda row: (
            _usable_length(row),
            1 if str(row.get("completeness")) == "full" else 0,
            -int(row["id"]),
        ),
        reverse=True,
    )
    return int(ordered[0]["id"])


def classify(rows: list[dict[str, Any]], page_url_set: set[str]) -> dict[str, list[Any]]:
    """纯判定:把文章行分成"同一篇文章的多份历史行"与"URL 身份可疑"两类。

    绝不在这一步写库,也不在这一步决定删除顺序 —— 交给 ``apply_plan``,方便体检复用。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        identity = _identity(row)
        if identity:
            groups.setdefault(identity, []).append(row)

    duplicate_groups: list[dict[str, Any]] = []
    identity_suspect: list[dict[str, Any]] = []
    for identity, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        ids = [int(row["id"]) for row in members]
        if identity in page_url_set:
            identity_suspect.append(
                {
                    "url_normalized": identity,
                    "ids": ids,
                    "reason": "来源页地址被写成文章地址(列表页抽取器把来源页赋给了每一条):"
                    "这些行不是重复,删了就毁掉语料;要修的是抽取器取每条自己的链接",
                }
            )
            continue
        titles = {str(row.get("title") or "").strip() for row in members}
        hashes = {str(row.get("content_hash") or "") for row in members}
        clusters = {str(row.get("cluster_id") or "") for row in members}
        clustered = len(clusters) == 1 and "" not in clusters
        if len(titles) == 1:
            same = "标题相同"
        elif len(hashes) == 1:
            same = "正文完全相同"
        elif clustered:
            # 清洗管线按 simhash 已经判定这两行几乎是同一篇文章(实测:同一篇新闻
            # 被更新过,标题变了、正文相似度极高)——这是数据自己的判断,不是猜的。
            same = f"清洗后正文同属一簇({next(iter(clusters))})"
        else:
            same = ""
        if same:
            keeper = _keeper_id(members)
            duplicate_groups.append(
                {
                    "url_normalized": identity,
                    "keeper_id": keeper,
                    "removed_ids": [one for one in ids if one != keeper],
                    "rows": members,
                    "reason": f"同一 URL 且{same}:同一篇文章的多份历史行",
                }
            )
        else:
            identity_suspect.append(
                {
                    "url_normalized": identity,
                    "ids": ids,
                    "reason": "同一 URL 下标题与正文都不同:URL 身份可疑"
                    "(可能是同一篇文章被更新过,也可能抽取器把不同文章指到同一 URL),不自动判重",
                }
            )
    return {"duplicate_groups": duplicate_groups, "identity_suspect": identity_suspect}


def read_rows(target: Path) -> list[dict[str, Any]]:
    con = store.read_only_connect(target)
    try:
        cursor = con.execute("SELECT * FROM articles ORDER BY id")
        columns = [str(entry[0]) for entry in cursor.description]
        return [dict(zip(columns, values)) for values in cursor.fetchall()]
    finally:
        con.close()


def plan_category(category: str, db_path: Path | None = None) -> dict[str, Any]:
    """单个命名空间的判重计划(只读,绝不写库)。"""
    target = Path(db_path) if db_path is not None else store.articles_db_path(category)
    plan: dict[str, Any] = {
        "category": category,
        "db": str(target),
        "duplicate_groups": [],
        "identity_suspect": [],
        "removable": 0,
    }
    if not target.exists():
        return plan
    classified = classify(read_rows(target), page_urls())
    # ``rows`` 里是整行原文,后面 apply/回滚都靠它,不再回库里捞
    plan["duplicate_groups"] = classified["duplicate_groups"]
    plan["identity_suspect"] = classified["identity_suspect"]
    plan["removable"] = sum(len(group["removed_ids"]) for group in plan["duplicate_groups"])
    return plan


def table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    if not table.replace("_", "").isalnum():  # 防御:表名只允许内置白名单拼进 SQL
        raise ValueError(f"unsupported table: {table!r}")
    return [str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")]


def _rows_of(con: sqlite3.Connection, table: str, article_id: int) -> list[dict[str, Any]]:
    columns = table_columns(con, table)
    if "article_id" not in columns:
        return []
    cursor = con.execute(f"SELECT * FROM {table} WHERE article_id = ?", (article_id,))
    return [dict(zip(columns, values)) for values in cursor.fetchall()]


def _count_of(con: sqlite3.Connection, table: str, article_id: int) -> int:
    return int(con.execute(f"SELECT COUNT(*) FROM {table} WHERE article_id = ?", (article_id,)).fetchone()[0])


def strip_references(
    con: sqlite3.Connection, article_id: int, keeper_id: int | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """删文章行之前先处理引用。返回 ``(搬移的引用, 丢弃并记账的引用, 删掉的 FTS 行数)``。

    ``keeper_id is None`` 表示同一篇文章的活体在**另一个类目库**里(跨命名空间拷贝):
    类目库物理隔离,引用不能跨库指向,只能丢弃并记账。
    """
    moved: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for table in PK_REFERENCE_TABLES:
        rows = _rows_of(con, table, article_id)
        if not rows:
            continue
        if keeper_id is not None and _count_of(con, table, keeper_id) == 0:
            con.execute(f"UPDATE {table} SET article_id = ? WHERE article_id = ?", (keeper_id, article_id))
            moved.append({"table": table, "from_id": article_id, "to_id": keeper_id, "rows": rows})
        else:
            con.execute(f"DELETE FROM {table} WHERE article_id = ?", (article_id,))
            dropped.extend({"table": table, "row": row} for row in rows)
    for table in ROW_REFERENCE_TABLES:
        rows = _rows_of(con, table, article_id)
        if not rows:
            continue
        if keeper_id is not None:
            con.execute(f"UPDATE {table} SET article_id = ? WHERE article_id = ?", (keeper_id, article_id))
            moved.append({"table": table, "from_id": article_id, "to_id": keeper_id, "rows": rows})
        else:
            con.execute(f"DELETE FROM {table} WHERE article_id = ?", (article_id,))
            dropped.extend({"table": table, "row": row} for row in rows)
    fts_rows = _count_of(con, FTS_TABLE, article_id)
    if fts_rows:
        # FTS 是派生索引,由 ``analysis.rag`` 全量重建;留陈旧条目会让检索命中不存在的文章
        con.execute(f"DELETE FROM {FTS_TABLE} WHERE article_id = ?", (article_id,))
    return moved, dropped, fts_rows


# ---------------------------------------------------------------------- 写入侧


def apply_plan(plan: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    """按计划在单个库内删除重复行(一个事务;逐行 CAS;账本可直接回滚)。"""
    target = Path(db_path) if db_path is not None else Path(str(plan["db"]))
    result: dict[str, Any] = {
        "category": plan.get("category"),
        "db": str(target),
        "removed": 0,
        "ledger": [],
        "moved_references": [],
        "dropped_references": [],
        "fts_rows_removed": 0,
        "identity_suspect": list(plan.get("identity_suspect") or []),
    }
    groups = list(plan.get("duplicate_groups") or [])
    if not groups:
        return result

    con = connect(target)
    try:
        con.execute("BEGIN IMMEDIATE")
        for group in groups:
            keeper_id = int(group["keeper_id"])
            for snapshot in group["rows"]:
                article_id = int(snapshot["id"])
                if article_id == keeper_id:
                    continue
                current = con.execute(
                    "SELECT content_hash FROM articles WHERE id = ?", (article_id,)
                ).fetchone()
                if current is None or str(current[0]) != str(snapshot.get("content_hash")):
                    raise DedupeConflict(
                        f"id={article_id} 在计划之后被改动过"
                        f"(计划里 content_hash={snapshot.get('content_hash')!r},"
                        f"库里是 {current[0] if current else None!r}),整笔回滚"
                    )
                moved, dropped, fts = strip_references(con, article_id, keeper_id)
                entry = dict(snapshot)
                entry["db"] = str(target)
                entry["keeper_id"] = keeper_id
                entry["moved_references"] = moved
                entry["dropped_references"] = dropped
                entry["fts_rows"] = fts
                con.execute("DELETE FROM articles WHERE id = ?", (article_id,))
                result["ledger"].append(entry)
                result["moved_references"].extend(moved)
                result["dropped_references"].extend(dropped)
                result["fts_rows_removed"] += fts
                result["removed"] += 1
        con.commit()
    except BaseException:
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        finally:
            con.close()
        raise
    con.close()
    LOG.warn(
        "dedupe.history_applied",
        category=result["category"],
        db=str(target),
        removed=result["removed"],
        moved_references=len(result["moved_references"]),
        dropped_references=len(result["dropped_references"]),
        fts_rows_removed=result["fts_rows_removed"],
        identity_suspect=len(result["identity_suspect"]),
    )
    return result


# ---------------------------------------------------------------- 跨命名空间


def plan_cross() -> dict[str, Any]:
    """跨命名空间的同一篇文章(``content_hash`` 相同)对账;只读。

    只按 ``content_hash`` 认定同一篇:同一 URL 不同内容跨库出现,是身份问题,
    不是拷贝问题,交给 ``identity_suspect`` 与抽取器整改,不自动删。
    """
    namespaces = store.namespaces_on_disk()
    registered = store.registered_namespaces()
    holders: dict[str, dict[str, list[int]]] = {}
    urls: dict[str, set[str]] = {}
    for name in namespaces:
        path = store.articles_db_path(name)
        if not path.exists():
            continue
        con = store.read_only_connect(path)
        try:
            for article_id, url_normalized, url, hash_value in con.execute(
                "SELECT id, url_normalized, url, content_hash FROM articles"
            ):
                key = str(hash_value or "")
                if not key:
                    continue
                holders.setdefault(key, {}).setdefault(name, []).append(int(article_id))
                urls.setdefault(key, set()).add(str(url_normalized or url or ""))
        finally:
            con.close()

    copies: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for hash_value, held_by in sorted(holders.items()):
        if len(held_by) < 2:
            continue
        entry = {
            "content_hash": hash_value,
            "url_normalized": sorted(urls.get(hash_value, set()))[:1] or "",
            "holders": {name: sorted(ids) for name, ids in sorted(held_by.items())},
        }
        owners = sorted(name for name in held_by if name in registered)
        if len(owners) >= 2:
            ambiguous.append({**entry, "reason": "两个及以上注册类目都持有同一篇文章,归属是人的决定,不自动删"})
        elif len(owners) == 1:
            copies.append(
                {
                    **entry,
                    "owner": owners[0],
                    "removable": [
                        {"category": name, "ids": sorted(ids)}
                        for name, ids in sorted(held_by.items())
                        if name not in registered
                    ],
                }
            )
        else:
            ambiguous.append({**entry, "reason": "没有任何注册类目持有,无法判定归属,不自动删"})
    return {"namespaces": namespaces, "copies": copies, "ambiguous": ambiguous}


def apply_cross(plan: dict[str, Any]) -> dict[str, Any]:
    """删除未注册命名空间里的跨库拷贝(每个命名空间一个事务,逐行 CAS)。

    跨库引用一律丢弃并记账:不能把 ``demo`` 库的标签指向 ``technology`` 库的 id。
    某个命名空间失败时,之前已提交的命名空间保持已提交,异常里说明是哪些,
    拿本次已写出的账本可以单独回滚。
    """
    result: dict[str, Any] = {"removed": 0, "ledger": [], "dropped_references": [], "applied_namespaces": []}
    # 计划里每条拷贝都记了 content_hash;写入时按 id→hash 逐行 CAS,
    # 行在计划之后被改过(hash 变了)就不是当初看到的那一条,拒绝删除。
    per_namespace: dict[str, dict[int, str]] = {}
    for copy in plan.get("copies") or []:
        hash_value = str(copy.get("content_hash") or "")
        for block in copy.get("removable") or []:
            bucket = per_namespace.setdefault(str(block["category"]), {})
            for article_id in block.get("ids") or []:
                bucket[int(article_id)] = hash_value

    for name, expected in sorted(per_namespace.items()):
        target = store.articles_db_path(name)
        if not expected or not target.exists():
            continue
        con = connect(target)
        ledger: list[dict[str, Any]] = []
        try:
            con.execute("BEGIN IMMEDIATE")
            columns = table_columns(con, "articles")
            for article_id in sorted(expected):
                cursor = con.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
                values = cursor.fetchone()
                if values is None:
                    raise DedupeConflict(f"{name} id={article_id} 在计划之后消失了,整笔回滚")
                snapshot = dict(zip(columns, values))
                if str(snapshot.get("content_hash") or "") != expected[article_id]:
                    raise DedupeConflict(
                        f"{name} id={article_id} 的 content_hash 与计划不符"
                        f"(计划 {expected[article_id]!r} / 现在 {snapshot.get('content_hash')!r}),整笔回滚"
                    )
                moved, dropped, fts = strip_references(con, article_id, None)
                if moved:  # 跨库不该出现搬移,出现就是实现走偏了
                    raise DedupeConflict(f"{name} id={article_id} 出现跨库引用搬移,拒绝写入")
                entry = dict(snapshot)
                entry["db"] = str(target)
                entry["moved_references"] = moved
                entry["dropped_references"] = dropped
                entry["fts_rows"] = fts
                con.execute("DELETE FROM articles WHERE id = ?", (article_id,))
                ledger.append(entry)
                result["dropped_references"].extend(dropped)
                result["removed"] += 1
            con.commit()
        except BaseException:
            try:
                con.rollback()
            except sqlite3.Error:
                pass
            finally:
                con.close()
            LOG.error(
                "dedupe.cross_failed",
                namespace=name,
                already_applied=result["applied_namespaces"],
                ledger_rows=len(ledger),
            )
            raise
        con.close()
        result["ledger"].extend(ledger)
        result["applied_namespaces"].append(name)
    LOG.warn(
        "dedupe.cross_applied",
        removed=result["removed"],
        namespaces=result["applied_namespaces"],
        dropped_references=len(result["dropped_references"]),
    )
    return result


# ------------------------------------------------------------------------ 回滚


def _ledger_of(report: Any) -> list[dict[str, Any]]:
    if isinstance(report, dict):
        return list(report.get("ledger") or [])
    if isinstance(report, list):
        return [entry for entry in report if isinstance(entry, dict)]
    raise TypeError(f"回滚需要账本(dict 或 list),得到 {type(report).__name__}")


def _insert_row(con: sqlite3.Connection, table: str, payload: dict[str, Any]) -> int:
    columns = [column for column in table_columns(con, table) if column in payload]
    if not columns:
        raise DedupeConflict(f"{table} 里没有可恢复的列,拒绝插回")
    placeholders = ",".join("?" * len(columns))
    cursor = con.execute(
        f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})",
        [payload[column] for column in columns],
    )
    return int(cursor.rowcount)


def rollback(report: Any) -> dict[str, Any]:
    """按账本把删掉的行与引用逐行恢复(含原 id)。FTS 行不恢复:那是派生索引,重跑重建即可。"""
    ledger = _ledger_of(report)
    by_db: dict[str, list[dict[str, Any]]] = {}
    for entry in ledger:
        category = str(entry.get("category") or "")
        target = str(entry.get("db") or "") or str(store.articles_db_path(category))
        by_db.setdefault(target, []).append(entry)

    result: dict[str, Any] = {"restored": 0, "references_restored": 0, "dbs": sorted(by_db), "skipped": []}
    for target, entries in sorted(by_db.items()):
        path = Path(target)
        if not path.exists():
            raise DedupeConflict(f"回滚目标库不存在:{target}")
        con = connect(path)
        try:
            con.execute("BEGIN IMMEDIATE")
            for entry in entries:
                article_id = int(entry["id"])
                if con.execute("SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone():
                    result["skipped"].append({"db": target, "id": article_id, "reason": "行还在,不重复插回"})
                    continue
                payload = {key: value for key, value in entry.items() if key not in LEDGER_EXTRA_KEYS}
                payload.pop("keeper_id", None)
                _insert_row(con, "articles", payload)
                result["restored"] += 1
                for item in entry.get("moved_references") or []:
                    table = str(item["table"])
                    rows = list(item.get("rows") or [])
                    to_id, from_id = int(item["to_id"]), int(item["from_id"])
                    if _count_of(con, table, to_id) != len(rows):
                        raise DedupeConflict(
                            f"{table} 里 article_id={to_id} 的行数({len(rows)} 期望)与账本不符,"
                            "可能有人已经改过分析结果,拒绝自动搬回"
                        )
                    con.execute(f"UPDATE {table} SET article_id = ? WHERE article_id = ?", (from_id, to_id))
                    result["references_restored"] += len(rows)
                for item in entry.get("dropped_references") or []:
                    _insert_row(con, str(item["table"]), dict(item.get("row") or {}))
                    result["references_restored"] += 1
            con.commit()
        except BaseException:
            try:
                con.rollback()
            except sqlite3.Error:
                pass
            finally:
                con.close()
            raise
        con.close()
    LOG.warn(
        "dedupe.rolled_back",
        restored=result["restored"],
        references_restored=result["references_restored"],
        dbs=result["dbs"],
    )
    return result


# ------------------------------------------------------------------ 体检复用


def summarize(namespaces: list[str] | None = None) -> dict[str, Any]:
    """给体检用的只读汇总:同类目重复行 + URL 身份可疑 + 跨命名空间拷贝。"""
    names = namespaces if namespaces is not None else store.namespaces_on_disk()
    duplicates: list[dict[str, Any]] = []
    suspects: list[dict[str, Any]] = []
    for name in names:
        plan = plan_category(name)
        for group in plan["duplicate_groups"]:
            duplicates.append(
                {
                    "category": name,
                    "url_normalized": group["url_normalized"],
                    "ids": group["removed_ids"],
                    "keeper_id": group["keeper_id"],
                    "reason": group["reason"],
                }
            )
        for suspect in plan["identity_suspect"]:
            suspects.append(
                {
                    "category": name,
                    "url_normalized": suspect["url_normalized"],
                    "ids": suspect["ids"],
                    "reason": suspect["reason"],
                }
            )
    cross = plan_cross()
    return {
        "duplicates": duplicates,
        "duplicate_rows": sum(len(one["ids"]) for one in duplicates),
        "identity_suspect": suspects,
        "cross_copies": len(cross["copies"]),
        "cross_copy_rows": sum(len(block["ids"]) for one in cross["copies"] for block in one["removable"]),
        "cross_ambiguous": len(cross["ambiguous"]),
    }
