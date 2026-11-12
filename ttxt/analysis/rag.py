"""类目 RAG:SQLite FTS5 + 中文双字词索引(免外部分词服务)。"""

from __future__ import annotations

from pathlib import Path

from ttxt import config as cfg
from ttxt.crawler.simhash import tokenize
from ttxt.db import connect

INDEX_CHAR_LIMIT = 8000


def bigrams(text: str) -> str:
    return " ".join(tokenize(text or ""))


def _db_path(category: str, db_path: Path | None) -> Path:
    return Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"


def rebuild_index(category: str, db_path: Path | None = None) -> dict[str, int]:
    target = _db_path(category, db_path)
    con = connect(target)
    try:
        rows = con.execute("SELECT id, title, cleaned_content, content FROM articles ORDER BY id").fetchall()
        con.execute("DELETE FROM articles_fts")
        con.executemany(
            "INSERT INTO articles_fts(article_id, title, body) VALUES (?, ?, ?)",
            [
                (
                    row["id"],
                    bigrams(row["title"] or ""),
                    bigrams((row["cleaned_content"] or row["content"] or "")[:INDEX_CHAR_LIMIT]),
                )
                for row in rows
            ],
        )
        con.commit()
    finally:
        con.close()
    return {"indexed": len(rows)}


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
