"""B1 授权指标导入:只允许白名单数据来源入库,防止编造指标。

规则(与 P10 评审口径一致):
- data_source 必须命中 config.metrics.authorized_sources 白名单,否则整批拒绝;
- 缺失/空值一律按 NULL 入库,绝不折算成 0(缺失 ≠ 表现差);
- 只允许写入本类目库能按 article_id 或规范化 URL 找到的文章(跨类目拒绝);
- 每次导入既更新 article_metrics 现值,也追加 metric_snapshots 时序快照;
- 单行错误只拒绝该行,并逐行报告原因。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ttxt import config as cfg
from ttxt.crawler.clean import normalize_url
from ttxt.db import connect

from . import viral

_INT_FIELDS = ("reads", "likes", "favorites", "comments", "impressions")


def load_authorized_sources() -> dict[str, str]:
    """{source_id: credibility},来自 config.metrics.authorized_sources。"""
    conf = cfg.load_config().get("metrics", {}) or {}
    raw = conf.get("authorized_sources", {}) or {}
    if isinstance(raw, list):  # 允许 [{"id":..., "credibility":...}] 写法
        return {str(item["id"]): str(item.get("credibility", "self_reported")) for item in raw}
    return {str(key): str(value) for key, value in raw.items()}


def _parse_int(value, field: str, row_no: int) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    text = str(value).strip()
    if text == "" or text.lower() in {"null", "none", "na", "n/a", "-"}:
        return None, None
    try:
        number = int(float(text)) if "." in text or "e" in text.lower() else int(text)
    except ValueError:
        return None, f"第 {row_no} 行 {field}={value!r} 不是整数"
    if number < 0:
        return None, f"第 {row_no} 行 {field}={number} 为负数"
    return number, None


def _read_rows(file_path: Path) -> list[dict]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"指标文件不存在:{path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON 指标文件必须是行数组")
        return [row for row in data if isinstance(row, dict)]
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    raise ValueError(f"仅支持 .csv/.json 指标文件,收到 {suffix or '(无扩展名)'}")


def _find_article(con, row: dict, category: str) -> int | None:
    article_ref = str(row.get("article_id") or row.get("id") or "").strip()
    if article_ref:
        hit = con.execute(
            "SELECT id FROM articles WHERE id=? AND category=?", (int(float(article_ref)), category)
        ).fetchone()
        return int(hit["id"]) if hit else None
    url = str(row.get("url") or "").strip()
    if url:
        hit = con.execute(
            "SELECT id FROM articles WHERE url_normalized=? AND category=?", (normalize_url(url), category)
        ).fetchone()
        return int(hit["id"]) if hit else None
    return None


def import_metrics(
    category: str,
    file_path: Path | str,
    data_source: str,
    db_path: Path | None = None,
    authorized: dict[str, str] | None = None,
    captured_at: str | None = None,
) -> dict:
    """导入一批真实指标。返回 {written, updated, snapshots, row_errors}。"""
    allow = authorized if authorized is not None else load_authorized_sources()
    source_id = str(data_source or "").strip()
    if source_id not in allow:
        raise PermissionError(
            f"未授权数据来源 {source_id!r}:不在 config.metrics.authorized_sources 白名单内,"
            "禁止入库(防止编造指标)。"
        )
    credibility = allow[source_id]
    rows = _read_rows(Path(file_path))
    if not rows:
        return {"written": 0, "updated": 0, "snapshots": 0, "row_errors": ["文件为空,无指标可导入"]}

    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    captured = captured_at or cfg.now_iso()
    written = updated = snapshots = 0
    row_errors: list[str] = []
    con = connect(target)
    try:
        for row_no, row in enumerate(rows, start=1):
            article_id = _find_article(con, row, category)
            if article_id is None:
                row_errors.append(f"第 {row_no} 行:文章不在 {category} 类目库(跨类目或不存在),拒绝写入")
                continue
            values: dict[str, int | None] = {}
            bad = False
            for field in _INT_FIELDS:
                number, err = _parse_int(row.get(field), field, row_no)
                if err:
                    row_errors.append(err)
                    bad = True
                    break
                values[field] = number
            if bad:
                continue
            if all(value is None for value in values.values()):
                row_errors.append(f"第 {row_no} 行:全部指标缺失,无有效数据,拒绝写入(缺失不伪造)")
                continue
            row_captured = str(row.get("captured_at") or "").strip() or captured
            baseline = str(row.get("account_baseline") or "").strip() or None
            exists = con.execute(
                "SELECT article_id FROM article_metrics WHERE article_id=?", (article_id,)
            ).fetchone()
            con.execute(
                "INSERT INTO article_metrics(article_id, reads, likes, favorites, comments, impressions,"
                " account_baseline, data_source, credibility, captured_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(article_id) DO UPDATE SET reads=excluded.reads, likes=excluded.likes,"
                " favorites=excluded.favorites, comments=excluded.comments, impressions=excluded.impressions,"
                " account_baseline=excluded.account_baseline, data_source=excluded.data_source,"
                " credibility=excluded.credibility, captured_at=excluded.captured_at",
                (
                    article_id,
                    values["reads"],
                    values["likes"],
                    values["favorites"],
                    values["comments"],
                    values["impressions"],
                    baseline,
                    source_id,
                    credibility,
                    row_captured,
                ),
            )
            con.execute(
                "INSERT INTO metric_snapshots(article_id, captured_at, reads, likes, favorites, comments,"
                " impressions, data_source) VALUES(?,?,?,?,?,?,?,?)",
                (
                    article_id,
                    row_captured,
                    values["reads"],
                    values["likes"],
                    values["favorites"],
                    values["comments"],
                    values["impressions"],
                    source_id,
                ),
            )
            snapshots += 1
            if exists:
                updated += 1
            else:
                written += 1
        con.commit()
    finally:
        con.close()
    return {
        "category": category,
        "data_source": source_id,
        "credibility": credibility,
        "written": written,
        "updated": updated,
        "snapshots": snapshots,
        "row_errors": row_errors,
    }


def snapshot_current_metrics(
    category: str,
    db_path: Path | None = None,
    captured_at: str | None = None,
) -> dict:
    """把当前 article_metrics 现值再冻结一份时序快照(日常追踪已登记文章)。"""
    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    captured = captured_at or cfg.now_iso()
    con = connect(target)
    try:
        rows = con.execute(
            "SELECT article_id, reads, likes, favorites, comments, impressions, data_source FROM article_metrics"
        ).fetchall()
        for row in rows:
            con.execute(
                "INSERT INTO metric_snapshots(article_id, captured_at, reads, likes, favorites, comments,"
                " impressions, data_source) VALUES(?,?,?,?,?,?,?,?)",
                (
                    row["article_id"],
                    captured,
                    row["reads"],
                    row["likes"],
                    row["favorites"],
                    row["comments"],
                    row["impressions"],
                    row["data_source"],
                ),
            )
        con.commit()
        return {"category": category, "captured_at": captured, "snapshots": len(rows)}
    finally:
        con.close()


def relabel_after_import(category: str, db_path: Path | None = None) -> dict:
    """导入后立即刷新爆款标签,保证分析口径同步。"""
    return viral.label_category(category, db_path=db_path)
