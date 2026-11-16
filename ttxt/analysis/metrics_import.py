"""B1 授权指标导入 + P21 指标合并:只允许白名单数据来源入库,防止编造/覆盖指标。

规则(与 P10 评审口径一致,并按 P21 收紧):
- data_source 必须命中 config.metrics.authorized_sources 白名单,否则整批拒绝;
- 缺失/空值一律按 NULL 入库,绝不折算成 0(缺失 ≠ 表现差);
- 只允许写入本类目库能按 article_id 或规范化 URL 找到的文章(跨类目拒绝);
- **字段级可信度合并**(规则 41):
  · 来值是 NULL ⇒ 绝不覆盖已有数值(缺证据不能把已知数据抹掉);
  · 来值可信度低于库里现值 ⇒ 保住强来源数值,只记 protected_by_credibility;
  · 可信度相同 ⇒ 较新的非空观测覆盖(同一来源刷新数据是正常语义);
  · 可信度更高 ⇒ 覆盖并补齐缺口;
  · 行上的 data_source/credibility 保留"最强贡献者",溯源不降级;
- 每次写入既更新 article_metrics 现值,也**追加** metric_snapshots 时序快照
  (快照记录这一批**实测到的原值**,包括 NULL,历史不可覆盖);
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

# 可信度分级(数值越大越可信)。config.metrics.credibility_rank 可覆盖/扩展。
DEFAULT_CREDIBILITY_RANK: dict[str, int] = {
    "verified": 5,        # 人工核验
    "self_reported": 4,   # 本人后台导出(头条后台)
    "public_page": 3,     # 公开文章页直接测到的数(点赞/评论)
    "public_card": 2,     # 公开卡片上的近似数(万级取整)
    "unverified": 1,      # 未核验
}


def load_authorized_sources() -> dict[str, str]:
    """{source_id: credibility},来自 config.metrics.authorized_sources。"""
    conf = cfg.load_config().get("metrics", {}) or {}
    raw = conf.get("authorized_sources", {}) or {}
    if isinstance(raw, list):  # 允许 [{"id":..., "credibility":...}] 写法
        return {str(item["id"]): str(item.get("credibility", "self_reported")) for item in raw}
    return {str(key): str(value) for key, value in raw.items()}


def credibility_rank(credibility: str | None) -> int:
    """来源可信度分级;白名单里没登记过的说法一律 0(最低,不许覆盖任何已登记来源)。"""
    key = str(credibility or "").strip().lower()
    ranks = dict(DEFAULT_CREDIBILITY_RANK)
    raw = (cfg.load_config().get("metrics", {}) or {}).get("credibility_rank") or {}
    if isinstance(raw, dict):
        for name, value in raw.items():
            try:
                ranks[str(name).strip().lower()] = int(value)
            except (TypeError, ValueError):
                continue
    return int(ranks.get(key, 0))


def _parse_int(value, field: str, row_no: int) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, f"第 {row_no} 行 {field}={value!r} 不是整数"
    if isinstance(value, int):
        if value < 0:
            return None, f"第 {row_no} 行 {field}={value} 为负数"
        return value, None
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


def write_metric_rows(
    category: str,
    rows: list[dict],
    data_source: str,
    db_path: Path | None = None,
    authorized: dict[str, str] | None = None,
    captured_at: str | None = None,
) -> dict:
    """写入一批实测指标(文件导入与页面采集共用)。

    返回 {written, updated, snapshots, filled_from_null, protected_by_credibility,
          null_kept, value_changed, row_errors}。
    """
    allow = authorized if authorized is not None else load_authorized_sources()
    source_id = str(data_source or "").strip()
    if source_id not in allow:
        raise PermissionError(
            f"未授权数据来源 {source_id!r}:不在 config.metrics.authorized_sources 白名单内,"
            "禁止入库(防止编造指标)。"
        )
    credibility = allow[source_id]
    new_rank = credibility_rank(credibility)
    if not rows:
        return {"written": 0, "updated": 0, "snapshots": 0, "changes": [], "row_errors": ["没有指标可写入"]}

    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    captured = captured_at or cfg.now_iso()
    written = updated = snapshots = 0
    filled_from_null = protected_by_credibility = null_kept = value_changed = 0
    row_errors: list[str] = []
    # P33:逐篇逐字段的"前值→后值"变更清单,让生产写入可事后按 article_id/来源/抓取时间对账。
    # 注意 fields[].new 记录的是**本轮来值**;是否真正生效由 action 说明:
    # inserted/filled_from_null/value_changed = 生效;unchanged = 没变;
    # null_kept = 本轮没证据但保住了已有值;protected_by_credibility = 来值可信度更低被保住;
    # stayed_null = 这个字段本来就没有,也依然没有(缺失绝不是 0)。
    changes: list[dict] = []
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
            old = con.execute(
                "SELECT reads, likes, favorites, comments, impressions, account_baseline,"
                " data_source, credibility FROM article_metrics WHERE article_id=?",
                (article_id,),
            ).fetchone()
            if old is None:
                merged = dict(values)
                ledger = [
                    {
                        "field": field,
                        "old": None,
                        "new": values[field],
                        "action": "inserted" if values[field] is not None else "stayed_null",
                    }
                    for field in _INT_FIELDS
                ]
                row_source, row_cred = source_id, credibility
                written += 1
                filled_from_null += sum(1 for value in merged.values() if value is not None)
            else:
                old_rank = credibility_rank(old["credibility"])
                merged = {}
                ledger = []
                for field in _INT_FIELDS:
                    incoming, stored = values[field], old[field]
                    if incoming is None:
                        merged[field] = stored
                        if stored is not None:
                            null_kept += 1
                            action = "null_kept"
                        else:
                            action = "stayed_null"
                    elif stored is None:
                        merged[field] = incoming
                        filled_from_null += 1
                        action = "filled_from_null"
                    elif new_rank >= old_rank:
                        merged[field] = incoming
                        if incoming != stored:
                            value_changed += 1
                            action = "value_changed"
                        else:
                            action = "unchanged"
                    else:
                        merged[field] = stored
                        protected_by_credibility += 1
                        action = "protected_by_credibility"
                    ledger.append({"field": field, "old": stored, "new": incoming, "action": action})
                if new_rank >= old_rank:
                    row_source, row_cred = source_id, credibility
                else:  # 弱来源不能把强来源的溯源降级
                    row_source, row_cred = old["data_source"], old["credibility"]
                    baseline = baseline or old["account_baseline"]
                updated += 1
            changes.append({
                "article_id": article_id,
                "captured_at": row_captured,
                "data_source": row_source,
                "credibility": row_cred,
                "fields": ledger,
            })
            con.execute(
                "INSERT INTO article_metrics(article_id, reads, likes, favorites, comments, impressions,"
                " account_baseline, data_source, credibility, captured_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(article_id) DO UPDATE SET reads=excluded.reads, likes=excluded.likes,"
                " favorites=excluded.favorites, comments=excluded.comments, impressions=excluded.impressions,"
                " account_baseline=excluded.account_baseline, data_source=excluded.data_source,"
                " credibility=excluded.credibility, captured_at=excluded.captured_at",
                (
                    article_id,
                    merged["reads"],
                    merged["likes"],
                    merged["favorites"],
                    merged["comments"],
                    merged["impressions"],
                    baseline,
                    row_source,
                    row_cred,
                    row_captured,
                ),
            )
            # 快照记录这一批**实测到的原值**(含 NULL):历史轨迹不被合并结果改写
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
        "filled_from_null": filled_from_null,
        "protected_by_credibility": protected_by_credibility,
        "null_kept": null_kept,
        "value_changed": value_changed,
        "changes": changes,
        "row_errors": row_errors,
    }


def import_metrics(
    category: str,
    file_path: Path | str,
    data_source: str,
    db_path: Path | None = None,
    authorized: dict[str, str] | None = None,
    captured_at: str | None = None,
) -> dict:
    """导入一批真实指标文件(委托给 write_metric_rows)。"""
    rows = _read_rows(Path(file_path))
    return write_metric_rows(
        category,
        rows,
        data_source,
        db_path=db_path,
        authorized=authorized,
        captured_at=captured_at,
    )


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
