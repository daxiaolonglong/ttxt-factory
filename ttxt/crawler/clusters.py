"""相似文章聚类:simhash 汉明距离 + 并查集,代表文章取最小 id。"""

from __future__ import annotations

from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect

from .simhash import hamming, simhash64, to_hex

DEFAULT_THRESHOLD = 3
LENGTH_RATIO_LIMIT = 2.5


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
) -> dict[str, int]:
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    con = connect(target)
    try:
        columns = {row[1] for row in con.execute("PRAGMA table_info(articles)")}
        has_cleaned = "cleaned_content" in columns
        sql = "SELECT id, title, content" + (", cleaned_content" if has_cleaned else "") + " FROM articles ORDER BY id"
        rows = con.execute(sql).fetchall()

        def text_of(row: object) -> str:
            # cleaned_content 已包含标题;只有缺失清洗结果时才回退到原始字段
            if has_cleaned and row["cleaned_content"]:
                return row["cleaned_content"]
            return f"{row['title'] or ''}\n{row['content'] or ''}"

        ids = [row["id"] for row in rows]
        lengths = {row["id"]: len(text_of(row)) for row in rows}
        hashes = {row["id"]: simhash64(text_of(row)) for row in rows}

        union = _UnionFind()
        pairs = 0
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                length_a, length_b = lengths[a], lengths[b]
                if length_a and length_b:
                    ratio = max(length_a, length_b) / min(length_a, length_b)
                    if ratio > LENGTH_RATIO_LIMIT:
                        continue
                if hamming(hashes[a], hashes[b]) <= threshold:
                    union.union(a, b)
                    pairs += 1

        groups: dict[int, list[int]] = {}
        for article_id in ids:
            groups.setdefault(union.find(article_id), []).append(article_id)

        now = cfg.now_iso()
        con.execute("DELETE FROM article_clusters")
        con.executemany(
            "UPDATE articles SET simhash = ?, cluster_id = ? WHERE id = ?",
            [(to_hex(hashes[aid]), f"c{min(members):06d}", aid) for members in groups.values() for aid in members],
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
    }
