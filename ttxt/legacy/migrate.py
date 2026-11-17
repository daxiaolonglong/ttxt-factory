"""旧资产迁移(P26 主线 / 账本 T-14):把 ``factory.db`` 的头条语料与真实计数搬进新体系类目库。

写入侧一律复用现成路径,不在这里另造一套去重:

- 文章走 ``crawler.store.insert_items``(URL 文章身份 + content_hash 双去重、跨命名空间互斥、
  正文升级/塌陷计数都在它里面);
- 指标走 ``analysis.metrics_import.write_metric_rows``(白名单来源、缺失 ≠ 0、按可信度合并、
  追加时序快照)。

P25 §九 的六条硬前置在这里的落点:①红名单测试(``10_tests/test_p37_legacy_migration.py``);
②类目映射只从 ``00_control/legacy_migration.json`` 读,代码里默认**空表**,未映射一律拒绝并计数;
③先只读碰撞预演(``apply=False`` 只读数不写一个字节);④新鲜度在 ``published_at`` 上归一化后可过滤;
⑥分批导入 + 每批备份 + 每批对账(第⑤条"管线增量化"是另一件事,见 ``08_wiki/PROJECT_STATUS.md``)。
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ttxt import config as cfg
from ttxt.analysis import metrics_import, rag
from ttxt.crawler import store
from ttxt.crawler.clean import content_hash, normalize_url
from ttxt.legacy import factory
from ttxt.logging import get_logger

LOG = get_logger("legacy.migrate")

POLICY_NAME = "legacy_migration.json"
DEFAULT_SOURCE_ID = "legacy_factory_search_ssr"
DEFAULT_LEGACY_DB = "资产库/01_语料库/factory.db"
# 旧采集把正文一律截到 8,000 字(P25 §九实测 max(length(body)) = 8000)。
# 这个数字必须进回执:它告诉下游"有多少行语料是被截断的",不能假装那是完整原文。
LEGACY_BODY_CAP = 8000
REJECT_REASONS = ("unmapped_category", "body_too_short", "no_body", "no_title", "bad_url", "over_limit")


@dataclass
class MigrationPolicy:
    """迁移口径。``category_map`` 默认空表:映射只能写在配置里,代码不许自己猜类目。"""

    source_id: str = DEFAULT_SOURCE_ID
    category_map: dict[str, str] = field(default_factory=dict)
    min_body_chars: int = 200
    zero_as_missing_fields: tuple[str, ...] = ("reads", "likes", "comments")
    max_rows_per_category: int | None = None
    legacy_db: str = DEFAULT_LEGACY_DB
    policy_path: str | None = None

    def category_for(self, primary_type: object) -> str | None:
        key = str(primary_type or "").strip()
        if not key:
            return None
        return self.category_map.get(key)


def load_policy(path: Path | str | None = None) -> MigrationPolicy:
    """读 ``00_control/legacy_migration.json``;文件不存在时返回空映射策略(= 什么都不迁)。"""
    target = Path(path) if path is not None else cfg.CONTROL_DIR / POLICY_NAME
    if not target.exists():
        return MigrationPolicy()
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{target} 必须是 JSON 对象")
    category_map = raw.get("category_map") or {}
    if not isinstance(category_map, dict):
        raise ValueError(f"{target}:category_map 必须是 {{旧标签: 类目slug}} 映射")
    for legacy_label, slug in category_map.items():
        if not store.SLUG_RE.match(str(slug or "")):
            # 类目隔离(规则 29):写错一个 slug 就会造出一个没人管的命名空间,必须当场拒绝
            raise ValueError(f"{target}:类目 slug {slug!r}(旧标签 {legacy_label!r})不是合法 slug")
    zero_fields = raw.get("zero_as_missing_fields", ["reads", "likes", "comments"])
    if not isinstance(zero_fields, list):
        raise ValueError(f"{target}:zero_as_missing_fields 必须是列表")
    return MigrationPolicy(
        source_id=str(raw.get("source_id") or DEFAULT_SOURCE_ID),
        category_map={str(k): str(v) for k, v in category_map.items()},
        min_body_chars=int(raw.get("min_body_chars", 200)),
        zero_as_missing_fields=tuple(str(v) for v in zero_fields),
        max_rows_per_category=(
            None if raw.get("max_rows_per_category") in (None, "") else int(raw["max_rows_per_category"])
        ),
        legacy_db=str(raw.get("legacy_db") or DEFAULT_LEGACY_DB),
        policy_path=str(target),
    )


def sha256_of_legacy_db(policy: MigrationPolicy, legacy_db: Path | str | None = None) -> str:
    return factory.sha256_file(resolve_legacy_db(policy, legacy_db))


def resolve_legacy_db(policy: MigrationPolicy, legacy_db: Path | str | None = None) -> Path:
    raw = Path(legacy_db) if legacy_db is not None else Path(policy.legacy_db)
    return raw if raw.is_absolute() else cfg.PROJECT_ROOT / raw


def _db_stats(db_path: str) -> dict[str, int]:
    con = store.read_only_connect(Path(db_path))
    try:
        return {
            "articles": int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]),
            "metrics": int(con.execute("SELECT COUNT(*) FROM article_metrics").fetchone()[0]),
            "snapshots": int(con.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0]),
            "fts": int(con.execute("SELECT COUNT(*) FROM articles_fts").fetchone()[0]),
        }
    finally:
        con.close()


class _IdentityIndex:
    """目标命名空间已有的 URL / content_hash 集合,只用于**只读预演**。

    跨命名空间互斥不在这里判:那条规则归 ``store.insert_items``(它会拦下别的注册类目已持有的
    文章),预演重复实现只会造成两套口径。所以预演只报"本命名空间会撞多少",
    跨命名空间的拦截数取 apply 时 ``insert_items`` 的真实回执。
    """

    def __init__(self, db_path: Path | str) -> None:
        self.urls: set[str] = set()
        self.hashes: set[str] = set()
        if not Path(db_path).exists():
            return
        con = store.read_only_connect(Path(db_path))
        con.row_factory = sqlite3.Row
        try:
            for row in con.execute("SELECT url_normalized, content_hash FROM articles"):
                self.urls.add(str(row["url_normalized"]))
                self.hashes.add(str(row["content_hash"]))
        finally:
            con.close()


def _novel_metric_rows(
    db_path: Path | str, category: str, rows: list[dict], source_id: str
) -> tuple[list[dict], int]:
    """过滤掉"这个来源已经写过、数值一字不差"的指标行,保证重跑迁移不追加重复快照。

    ``metrics_import`` 每次写入都会追加一条时序快照(这是它的设计,不改它);
    迁移是一次性回填,重跑时必须自己少写,否则每重跑一次就多一屏假历史。

    返回 ``(待写行, 孤儿数)``:孤儿是"正文被去重掉了、这个命名空间里没有对应文章"的计数行,
    它们没有可挂靠的 ``article_id``,写下去只会造出一条无主观测 ⇒ 单独计数,不静默丢弃。
    """
    if not rows:
        return [], 0
    con = store.read_only_connect(Path(db_path))
    con.row_factory = sqlite3.Row
    try:
        stored: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for r in con.execute(
            """
            SELECT a.url_normalized AS url_normalized, m.data_source AS data_source,
                   m.reads AS reads, m.likes AS likes, m.comments AS comments,
                   m.favorites AS favorites, m.impressions AS impressions
            FROM articles a LEFT JOIN article_metrics m ON m.article_id = a.id
            WHERE a.category = ?
            """,
            (category,),
        ):
            stored[str(r["url_normalized"])].append(r)
    finally:
        con.close()
    novel: list[dict] = []
    orphans = 0
    for row in rows:
        key = normalize_url(str(row.get("url") or ""))
        candidates = stored.get(key)
        if not candidates:
            orphans += 1
            continue
        same = any(
            str(hit["data_source"]) == source_id
            and all(
                row.get(field) is None or row.get(field) == hit[field]
                for field in ("reads", "likes", "comments", "favorites", "impressions")
            )
            for hit in candidates
        )
        if not same:
            novel.append(row)
    return novel, orphans


def run(
    policy: MigrationPolicy,
    *,
    legacy_db: Path | str | None = None,
    apply: bool = False,
    batch_size: int = 500,
    limit: int | None = None,
    body_cap: int = LEGACY_BODY_CAP,
    db_path_for: Callable[[str], str] | None = None,
    backup_dir: Path | str | None = None,
    receipt_path: Path | str | None = None,
    captured_at: str | None = None,
) -> dict:
    """一趟流式扫描:预演(``apply=False``)或分批导入(``apply=True``)。

    内存有界:逐行流式读取旧库,按类目攒到 ``batch_size`` 就落盘,绝不把 5 万行正文读进内存。
    """
    if batch_size <= 0:
        raise ValueError("batch_size 必须为正数")
    legacy_path = resolve_legacy_db(policy, legacy_db)
    if not legacy_path.exists():
        raise FileNotFoundError(f"旧库不存在:{legacy_path}")
    sha_before = factory.sha256_file(legacy_path)

    def target_path(category: str) -> Path:
        """目标类目库路径。**只解析、不创建**:预演绝不允许顺手造出一个命名空间。"""
        if db_path_for is not None:
            return Path(db_path_for(category))
        return store.articles_db_path(category)

    def ensure_schema(category: str) -> Path:
        # 只有 apply 才建/补结构:类目库必须先是全 schema 的,否则 sqlite 会造出一个没有
        # 指标表和分析表的空库(这条路径与 pipeline/analyze 用的是同一个 migrate)。
        return store.migrate_articles_db(category, db_path=target_path(category))

    receipt: dict[str, object] = {
        "mode": "apply" if apply else "dry_run",
        "source_id": policy.source_id,
        "policy_path": policy.policy_path,
        "category_map": dict(policy.category_map),
        "min_body_chars": policy.min_body_chars,
        "zero_as_missing_fields": list(policy.zero_as_missing_fields),
        "legacy_db": str(legacy_path),
        "legacy_sha256_before": sha_before,
        "legacy_sha256_after": None,
        "legacy_rows_scanned": 0,
        "by_primary_type": defaultdict(int),
        "by_category": defaultdict(int),
        "rejected": defaultdict(int),
        "rejected_detail": defaultdict(lambda: defaultdict(int)),
        "would_insert": 0,
        "would_collide_same_namespace": 0,
        "would_collide_within_migration": 0,
        "inserted": 0,
        "duplicates": 0,
        "bodies_upgraded": 0,
        "bodies_kept_existing": 0,
        "url_identity_conflict": 0,
        "cross_namespace_blocked": 0,
        "metrics_written": 0,
        "metrics_updated": 0,
        "metrics_skipped_already_same": 0,
        "metrics_orphaned": 0,
        "snapshots_appended": 0,
        "body_at_legacy_cap": 0,
        "indexed": 0,
        "index_errors": [],
        "batches": [],
        "errors": [],
    }
    if not policy.category_map:
        receipt["errors"].append(
            "category_map 为空:映射必须写在 00_control/legacy_migration.json 里,拒绝迁移"
        )

    identities: dict[str, _IdentityIndex] = {}
    run_seen: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"urls": set(), "hashes": set()})
    buffers: dict[str, list[tuple[object, dict | None]]] = defaultdict(list)
    written_per_category: dict[str, int] = defaultdict(int)
    backed_up: set[str] = set()

    def index_for(category: str) -> _IdentityIndex:
        if category not in identities:
            identities[category] = _IdentityIndex(target_path(category))
        return identities[category]

    def flush(category: str) -> None:
        pending = buffers.get(category) or []
        if not pending:
            return
        buffers[category] = []
        items = [item for item, _metrics in pending]
        metrics_rows = [metrics for _item, metrics in pending if metrics]
        entry: dict[str, object] = {
            "category": category,
            "batch_size": len(items),
            "at": cfg.now_iso(),
        }
        if not apply:
            idx = index_for(category)
            seen = run_seen[category]
            would = 0
            collided = 0
            collided_in_run = 0
            for item in items:
                # item.url 已由 factory.to_item 规范化;库里存的是 url_normalized ⇒ 同一口径才可比
                url_normalized = str(item.url)
                digest = content_hash(item.title, item.content)
                if url_normalized in idx.urls or digest in idx.hashes:
                    collided += 1
                elif url_normalized in seen["urls"] or digest in seen["hashes"]:
                    # 旧库里同一篇正文挂在多个 gid 下(旧采集按 gid 存,一篇稿多发)
                    collided_in_run += 1
                else:
                    would += 1
                seen["urls"].add(url_normalized)
                seen["hashes"].add(digest)
            entry["would_insert"] = would
            entry["would_collide_same_namespace"] = collided
            entry["would_collide_within_migration"] = collided_in_run
            receipt["batches"].append(entry)
            return

        db_path = target_path(category)
        if backup_dir is not None and category not in backed_up and db_path.exists():
            # 备份必须在补结构之前:拿到的是"迁移开始前"的那一份,回滚才有意义
            backup_file = backup_category_db(str(db_path), Path(backup_dir), category)
            backed_up.add(category)
            entry["backup"] = backup_file
        db_path = ensure_schema(category)
        if backup_dir is not None and category not in backed_up and Path(db_path).exists():
            backup_file = backup_category_db(db_path, Path(backup_dir), category)
            backed_up.add(category)
            entry["backup"] = backup_file

        before = _db_stats(db_path)
        cross_report: dict[str, object] = {}
        counts = store.insert_items(
            category, policy.source_id, items, db_path=Path(db_path), cross_report=cross_report
        )
        fresh, orphans = _novel_metric_rows(db_path, category, metrics_rows, policy.source_id)
        metric_result = {"written": 0, "updated": 0, "snapshots": 0, "row_errors": []}
        if fresh:
            metric_result = metrics_import.write_metric_rows(
                category,
                fresh,
                policy.source_id,
                db_path=Path(db_path),
                captured_at=captured_at or cfg.now_iso(),
            )
        after = _db_stats(db_path)
        receipt["inserted"] = int(receipt["inserted"]) + int(counts.get("inserted", 0))
        receipt["duplicates"] = int(receipt["duplicates"]) + int(counts.get("duplicates", 0))
        receipt["bodies_upgraded"] = int(receipt["bodies_upgraded"]) + int(counts.get("bodies_upgraded", 0))
        receipt["bodies_kept_existing"] = int(receipt["bodies_kept_existing"]) + int(
            counts.get("bodies_kept_existing", 0)
        )
        receipt["url_identity_conflict"] = int(receipt["url_identity_conflict"]) + int(
            counts.get("url_identity_conflict", 0)
        )
        receipt["cross_namespace_blocked"] = int(receipt["cross_namespace_blocked"]) + int(
            cross_report.get("blocked", 0)
        )
        receipt["metrics_written"] = int(receipt["metrics_written"]) + int(metric_result.get("written", 0))
        receipt["metrics_updated"] = int(receipt["metrics_updated"]) + int(metric_result.get("updated", 0))
        receipt["metrics_skipped_already_same"] = int(receipt["metrics_skipped_already_same"]) + (
            len(metrics_rows) - len(fresh) - orphans
        )
        receipt["metrics_orphaned"] = int(receipt["metrics_orphaned"]) + orphans
        receipt["snapshots_appended"] = int(receipt["snapshots_appended"]) + int(
            metric_result.get("snapshots", 0)
        )
        entry["insert_items"] = counts
        entry["metrics"] = {
            "written": metric_result.get("written", 0),
            "updated": metric_result.get("updated", 0),
            "snapshots": metric_result.get("snapshots", 0),
            "skipped_already_same": len(metrics_rows) - len(fresh) - orphans,
            "orphaned": orphans,
            "row_errors": list(metric_result.get("row_errors") or [])[:20],
        }
        entry["reconcile"] = {"before": before, "after": after, "delta": {
            key: after[key] - before[key] for key in after
        }}
        receipt["batches"].append(entry)
        for err in metric_result.get("row_errors") or []:
            receipt["errors"].append(f"{category}: {err}")
        LOG.info(
            "legacy.batch",
            mode="apply",
            category=category,
            batch=len(items),
            inserted=counts.get("inserted", 0),
            duplicates=counts.get("duplicates", 0),
            metrics_written=metric_result.get("written", 0),
            metrics_updated=metric_result.get("updated", 0),
            detail="旧资产分批导入(类目库前后计数见回执)",
        )

    con = factory.open_read_only(legacy_path)
    try:
        for record in factory.iter_articles(con, limit=limit):
            receipt["legacy_rows_scanned"] = int(receipt["legacy_rows_scanned"]) + 1
            label = str(record.get("primary_type") or "").strip() or "(空)"
            receipt["by_primary_type"][label] += 1
            if int(record.get("body_len") or 0) >= body_cap:
                receipt["body_at_legacy_cap"] = int(receipt["body_at_legacy_cap"]) + 1

            category = policy.category_for(record.get("primary_type"))
            if category is None:
                receipt["rejected"]["unmapped_category"] += 1
                receipt["rejected_detail"]["unmapped_category"][label] += 1
                continue

            item, reason = factory.to_item(record, min_body_chars=policy.min_body_chars)
            if item is None:
                receipt["rejected"][reason or "no_body"] += 1
                receipt["rejected_detail"][reason or "no_body"][label] += 1
                continue

            cap = policy.max_rows_per_category
            if cap is not None and written_per_category[category] >= cap:
                receipt["rejected"]["over_limit"] += 1
                continue
            written_per_category[category] += 1
            receipt["by_category"][category] += 1
            buffers[category].append((item, factory.metrics_row(record, zero_as_missing=policy.zero_as_missing_fields)))
            for bucket in buffers:
                if len(buffers[bucket]) >= batch_size and bucket != category:
                    flush(bucket)
            if len(buffers[category]) >= batch_size:
                flush(category)
        for bucket in sorted(buffers):
            flush(bucket)
    finally:
        con.close()

    if not apply:
        for entry in receipt["batches"]:
            receipt["would_insert"] = int(receipt["would_insert"]) + int(entry.get("would_insert", 0))
            receipt["would_collide_same_namespace"] = int(receipt["would_collide_same_namespace"]) + int(
                entry.get("would_collide_same_namespace", 0)
            )
            receipt["would_collide_within_migration"] = int(
                receipt["would_collide_within_migration"]
            ) + int(entry.get("would_collide_within_migration", 0))
        receipt["notes"] = [
            "预演只读:没有建库、没有写一个字节(跨命名空间拦截数只有 apply 时 insert_items 能给)",
        ]
    else:
        # 索引必须在迁移当场补上,不能等 `ttxt analyze` —— P25 的教训(采集路径漏建 FTS,
        # 当天语料在 `ttxt search` 里看不见)在迁移路径上同样成立。
        # 正文已在 factory.to_item 侧清洗过,rag 的 cleaned_content 回退到 content 不影响质量。
        # simhash 聚类不在这里跑:cluster_category 是 O(n²) 两两比对,4 万行会跑挂,
        # 它是 P26 待办2「管线增量化」的一部分,回执里如实写明。
        for category in sorted(set(receipt["by_category"])):
            try:
                stats = rag.rebuild_index(category, db_path=target_path(category))
                receipt["indexed"] = int(receipt["indexed"]) + int(stats["indexed"])
            except Exception as exc:  # noqa: BLE001 - 索引失败必须可见,但不能让已导入的语料回滚
                receipt["index_errors"].append(f"{category}: {exc}")
        receipt["notes"] = [
            "已导入语料当场进 FTS 索引(ttxt search 立即可见)",
            "simhash 聚类与 analyze 派生列未跑:cluster_category 是 O(n²),4 万行需要增量化(P26 待办2)",
        ]

    sha_after = factory.sha256_file(legacy_path)
    receipt["legacy_sha256_after"] = sha_after
    if sha_after != sha_before:
        # 旧库是只读资产(规则 33);一字之差就是事故,必须当场炸,不许继续往下游污染
        raise RuntimeError(f"旧库被改动了:{sha_before} → {sha_after}")
    receipt["by_primary_type"] = dict(receipt["by_primary_type"])
    receipt["by_category"] = dict(receipt["by_category"])
    receipt["rejected"] = dict(receipt["rejected"])
    receipt["rejected_detail"] = {k: dict(v) for k, v in receipt["rejected_detail"].items()}
    LOG.info(
        "legacy.run",
        mode=receipt["mode"],
        scanned=receipt["legacy_rows_scanned"],
        accepted=sum(receipt["by_category"].values()),
        rejected=sum(receipt["rejected"].values()),
        inserted=receipt["inserted"],
        indexed=receipt["indexed"],
        legacy_sha256=sha_after[:12],
        detail="旧资产迁移一趟跑完(预演不写库,apply 才写)",
    )
    if receipt_path is not None:
        target = Path(receipt_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        receipt["receipt_path"] = str(target)
    return receipt


def backup_category_db(db_path: str, backup_dir: Path, category: str) -> str:
    """每批首次写入前备份该类目库(sqlite 在线备份 API,WAL 下也一致)。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = cfg.now_iso().replace(":", "").replace("+", "")
    target = backup_dir / f"{category}-before-{stamp}.db"
    src = store.connect(Path(db_path))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return str(target)
