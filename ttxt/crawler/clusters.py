"""相似文章聚类:simhash 汉明距离 + 并查集,代表文章取最小 id。

T-17(账本实测):social 40,506 行时 O(n²) 两两比对(≈8.2 亿对)是 clean 47m51s 的大头,
且每轮都从零重算 simhash。这里的增量化**不跳过任何一对真正的近重复**:

- 把 64 位哈希切成 ``threshold + 1`` 个连续位块(阈值 3 ⇒ 4×16 位)。鸽笼原理:
  两条哈希汉明距离 ≤ threshold 时,至少有一个位块逐位相同。于是"同块同值"桶的并集
  **保证覆盖**所有汉明距离 ≤ threshold 的对 —— 是超集,不是概率(不是 LSH)。
- 桶内候选再过**与暴力路径完全相同**的两道过滤(长度比 LENGTH_RATIO_LIMIT 预筛 +
  hamming ≤ threshold),喂给同一个 _UnionFind。⇒ 对集合、簇、计数与暴力路径逐条相等。
- 未变行(指纹台账 pipeline_state 里 content_fp 相同)直接复用台账里的 simhash 与
  text_len,不再重算哈希;写回 articles 只对取值真的变了的行发 UPDATE(WAL/磁盘)。
- 回执里给出 candidate_pairs_examined vs brute_force_pair_budget 与 oversized_buckets,
  谁也不能拿"偷偷跳过聚类"当提速手段而不留证据。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect

from .simhash import from_hex, hamming, simhash64, to_hex

DEFAULT_THRESHOLD = 3
LENGTH_RATIO_LIMIT = 2.5
# 位块数 = threshold+1,threshold≥32 时位块切不出 64 位覆盖(每块至少 1 位),
# 分桶失去鸽笼前提 ⇒ 回退暴力双循环,回执里如实标注 brute_force_fallback。
BRUTE_FORCE_FALLBACK_THRESHOLD = 32


def content_fingerprint(clean_version: str, title: str | None, content: str | None) -> str:
    """管线自己的源文本指纹。

    故意**不用** ``articles.content_hash``:正确性不能依赖每个写入方(migrate/dedupe/
    人工整改…)都记得同步那一列。指纹覆盖 ``CLEAN_VERSION``,清洗规则一升级,全部行
    自动失效重算。
    """
    payload = f"{clean_version}\n{title or ''}\n{content or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _block_bounds(blocks: int) -> list[tuple[int, int]]:
    """把 64 位切成 blocks 个连续位块,返回 (shift, width)。

    64 不能整除时余数摊给前面的块(每块至少 1 位,调用方保证 blocks ≤ 64);
    位块划分必须完整覆盖 64 位,鸽笼原理才成立。
    """
    base, remainder = divmod(64, blocks)
    bounds: list[tuple[int, int]] = []
    shift = 0
    for index in range(blocks):
        width = base + (1 if index < remainder else 0)
        bounds.append((shift, width))
        shift += width
    return bounds


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, value: int) -> int:
        self.parent.setdefault(value, value)
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[value] != root:
            self.parent[value], value = root, self.parent[value]
        return root

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def cluster_category(
    category: str,
    threshold: int = DEFAULT_THRESHOLD,
    db_path: Path | None = None,
    method: str = "bucketed",
    clean_version: str | None = None,
    report_over: int = 500,
) -> dict:
    """聚类并写回 simhash/cluster_id/article_clusters。

    - ``method``:"bucketed"(默认,位块分桶,结果与暴力**逐条相等**)或 "brute"
      (原 O(n²) 双循环,保留可达入口,给测试在同一语料上交叉核对用)。
    - ``clean_version``:传入时启用指纹台账复用 —— pipeline_state 里 content_fp 与
      当前 ``(clean_version, title, content)`` 指纹一致的行走台账 simhash/text_len,
      其余行重算。不传(老调用方)则全部重算,行为与改造前一致。
    - ``report_over``:桶大小超过该值写进 ``oversized_buckets`` 回执(哈希碰撞把大量
      行挤进同一个桶的信号),必须报告,不得隐藏。
    """
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    started = time.perf_counter()
    con = connect(target)
    try:
        columns = {row[1] for row in con.execute("PRAGMA table_info(articles)")}
        has_cleaned = "cleaned_content" in columns
        sql = "SELECT id, title, content" + (", cleaned_content" if has_cleaned else "") + " FROM articles ORDER BY id"

        def text_of(row: object) -> str:
            # cleaned_content 已包含标题;只有缺失清洗结果时才回退到原始字段
            if has_cleaned and row["cleaned_content"]:
                return row["cleaned_content"]
            return f"{row['title'] or ''}\n{row['content'] or ''}"

        # 指纹台账:content_fp 一致 ⇒ simhash/text_len 可复用(与管线共用同一指纹函数)
        ledger: dict[int, tuple[str, str, int]] = {}
        if clean_version is not None:
            has_ledger = (
                con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pipeline_state'")
                .fetchone()
                is not None
            )
            if has_ledger:
                ledger = {
                    int(row["article_id"]): (str(row["content_fp"]), str(row["simhash"]), int(row["text_len"] or 0))
                    for row in con.execute("SELECT article_id, content_fp, simhash, text_len FROM pipeline_state")
                }

        ids: list[int] = []
        lengths: dict[int, int] = {}
        hashes: dict[int, int] = {}
        hashes_reused = 0
        for row in con.execute(sql):
            article_id = int(row["id"])
            ids.append(article_id)
            entry = ledger.get(article_id)
            if entry is not None and entry[0] == content_fingerprint(clean_version, row["title"], row["content"]):
                hashes[article_id] = from_hex(entry[1])
                lengths[article_id] = entry[2]
                hashes_reused += 1
                continue
            text = text_of(row)
            lengths[article_id] = len(text)
            hashes[article_id] = simhash64(text)

        union = _UnionFind()
        pairs = 0
        candidates = 0

        def consider(a: int, b: int) -> None:
            # 与改造前的暴力循环逐字同判:长度比预筛(任一长度为 0 时不设比例门槛)
            # + 精确汉明距离;接受的进同一个并查集。
            nonlocal pairs
            length_a, length_b = lengths[a], lengths[b]
            if length_a and length_b:
                ratio = max(length_a, length_b) / min(length_a, length_b)
                if ratio > LENGTH_RATIO_LIMIT:
                    return
            if hamming(hashes[a], hashes[b]) <= threshold:
                union.union(a, b)
                pairs += 1

        use_bucketed = method == "bucketed" and threshold < BRUTE_FORCE_FALLBACK_THRESHOLD
        brute_force_fallback = method == "bucketed" and not use_bucketed
        effective_method = "bucketed" if use_bucketed else "brute"
        oversized: list[dict] = []
        blocks = min(threshold + 1, 64) if use_bucketed else 0

        if use_bucketed:
            # 鸽笼:hamming ≤ threshold ⇒ 至少一个位块完全一致 ⇒ 桶并集是近重复对的超集。
            #
            # R28(2026-11-18)性能修复:旧实现用**全局 seen 集合**记录"这一对已经检查过",
            # 集合大小 = 候选对数。500k 行压测实测把进程私有内存顶到 **15.17 GB**
            # (工作集只有 0.89 GB ⇒ 几乎全在被换页),cluster 阶段 CPU 占用跌到 5% 级,
            # 45 分钟没有任何写盘,等于卡死。候选对数随 n 增长,不该用 O(候选对数) 的内存。
            #
            # 现改为**逐文章取候选**:每块先建桶(内存 O(n)),再按 id 升序遍历文章 a,
            # 从各块里取出与 a 同桶的 id 集合(局部 set,大小 = 该文章所在桶的规模),
            # 只保留 id > a 的 b ⇒ **每对无序组合恰好检查一次**,不需要全局 seen,
            # candidate_pairs_examined 与旧实现逐个相等(去重语义不变)。
            # 桶表同时保留,便于如实统计 oversized_buckets(report_over 以上必须报,不得隐藏)。
            bounds = _block_bounds(blocks)
            block_buckets: list[dict[int, list[int]]] = []
            for block_index, (shift, width) in enumerate(bounds):
                mask = (1 << width) - 1
                buckets: dict[int, list[int]] = {}
                for article_id in ids:
                    buckets.setdefault((hashes[article_id] >> shift) & mask, []).append(article_id)
                for bucket in buckets.values():
                    if len(bucket) > report_over:
                        oversized.append({"block": block_index, "shift": shift, "size": len(bucket)})
                block_buckets.append(buckets)
            for a in ids:
                candidates_for_a: set[int] = set()
                for (shift, width), buckets in zip(bounds, block_buckets):
                    mask = (1 << width) - 1
                    bucket = buckets.get((hashes[a] >> shift) & mask)
                    if bucket:
                        candidates_for_a.update(bucket)
                for b in candidates_for_a:
                    if b <= a:  # ids 由 ORDER BY id 升序给出 ⇒ 只取后半,每对恰好一次
                        continue
                    candidates += 1
                    consider(a, b)
        else:
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    candidates += 1
                    consider(ids[i], ids[j])

        groups: dict[int, list[int]] = {}
        for article_id in ids:
            groups.setdefault(union.find(article_id), []).append(article_id)

        now = cfg.now_iso()
        # 写回只对取值真变了的行发 UPDATE(写放大对 WAL/磁盘敏感);article_clusters 照旧整表重写(小表)。
        current = {
            int(row["id"]): (row["simhash"], row["cluster_id"])
            for row in con.execute("SELECT id, simhash, cluster_id FROM articles")
        }
        updates: list[tuple[str, str, int]] = []
        for members in groups.values():
            cluster_id = f"c{min(members):06d}"
            for article_id in members:
                want = (to_hex(hashes[article_id]), cluster_id)
                if current.get(article_id) != want:
                    updates.append((want[0], want[1], article_id))
        con.execute("DELETE FROM article_clusters")
        con.executemany(
            "UPDATE articles SET simhash = ?, cluster_id = ? WHERE id = ?",
            updates,
        )
        con.executemany(
            """
            INSERT INTO article_clusters(
              cluster_id, category, representative_id, member_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (f"c{min(members):06d}", category, min(members), len(members), now, now)
                for members in groups.values()
            ],
        )
        con.commit()
    finally:
        con.close()

    return {
        "articles": len(ids),
        "clusters": len(groups),
        "near_duplicate_pairs": pairs,
        "singletons": sum(1 for members in groups.values() if len(members) == 1),
        # T-17 审计回执:加速必须可核对 —— candidate_pairs_examined 对比暴力预算,
        # oversized_buckets 把哈希碰撞集中度如实报出来,不许藏。
        "candidate_pairs_examined": candidates,
        "brute_force_pair_budget": len(ids) * (len(ids) - 1) // 2,
        "buckets_built": blocks,
        "oversized_buckets": oversized,
        "method": effective_method,
        "brute_force_fallback": brute_force_fallback,
        "hashes_reused": hashes_reused,
        "hashes_recomputed": len(ids) - hashes_reused,
        "writeback_updates": len(updates),
        "elapsed_s": round(time.perf_counter() - started, 3),
    }
