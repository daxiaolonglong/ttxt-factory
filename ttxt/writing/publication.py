"""R41:草稿 → 发布链接 → 表现数据的绑定链路,以及 T+N 口径。

T+N 定义(本轮起替代「采集时点快照」口径):

- ``T`` = 文章真实发布时间 ``published_at``(无法解析为确定时间就拒绝绑定,绝不猜)。
- ``due_at`` = ``T + N 天``(N 默认 3,可由环境变量 ``TTXT_PERFORMANCE_WINDOW_DAYS``
  或 ``config.metrics.performance_window_days`` 覆盖)。
- 有效读数窗口 = ``[due_at, due_at + 24h]``;窗口内有多条观测时取**最早**的一条。
- 窗口外的观测只作为事实保留,不冒充 T+N 读数:
  · 还没到 ``due_at`` ⇒ ``pending`` / ``early``(只有提前观测);
  · 过了窗口才有观测 ⇒ ``late``;过了窗口且没有任何到期后观测 ⇒ ``overdue``;
  · 一条观测都没有 ⇒ ``unavailable``。
- 任何指标缺失一律存 ``NULL``,绝不以 0 冒充(缺失 ≠ 表现差)。

链路三张表(每个类目库内,幂等建表):

- ``publications``:草稿 → 发布链接 → 发布时间(可回溯到 draft_id/version)。
- ``publication_observations``:发布维度的原始时序观测(采集到的原值,不覆盖历史)。
- ``performance_windows``:按 T+N 选点后的读数,``subject_type`` 区分
  ``publication``(已绑定发布)与 ``article``(历史文章按可得信息回填)。

本轮只做数据结构与绑定链路,评分的连续性/去模板化不动;``draft_id`` 与
``publication_id`` 留在窗口行上,供后续评分对照直接 join,不必再改口径。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from ttxt import config as cfg
from ttxt.crawler import timeparse
from ttxt.crawler.clean import normalize_url
from ttxt.db import connect

DEFAULT_WINDOW_DAYS = 3
WINDOW_GRACE_HOURS = 24
WINDOW_DAYS_ENV = "TTXT_PERFORMANCE_WINDOW_DAYS"

METRIC_FIELDS = ("reads", "likes", "favorites", "comments", "impressions")

STATUS_ON_TIME = "on_time"
STATUS_LATE = "late"
STATUS_EARLY = "early"
STATUS_PENDING = "pending"
STATUS_OVERDUE = "overdue"
STATUS_UNAVAILABLE = "unavailable"
ALL_STATUSES = (
    STATUS_ON_TIME,
    STATUS_LATE,
    STATUS_EARLY,
    STATUS_PENDING,
    STATUS_OVERDUE,
    STATUS_UNAVAILABLE,
)

PUBLICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  draft_id TEXT NOT NULL,
  version INTEGER,
  platform TEXT,
  url TEXT NOT NULL,
  url_normalized TEXT NOT NULL,
  published_at TEXT NOT NULL,
  published_at_source TEXT NOT NULL DEFAULT 'declared',
  bound_at TEXT NOT NULL,
  note TEXT,
  UNIQUE(url_normalized)
);
CREATE INDEX IF NOT EXISTS idx_publications_draft ON publications(draft_id);

CREATE TABLE IF NOT EXISTS publication_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  publication_id INTEGER NOT NULL,
  captured_at TEXT NOT NULL,
  reads INTEGER,
  likes INTEGER,
  favorites INTEGER,
  comments INTEGER,
  impressions INTEGER,
  data_source TEXT NOT NULL,
  note TEXT,
  UNIQUE(publication_id, captured_at, data_source)
);

CREATE TABLE IF NOT EXISTS performance_windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject_type TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  category TEXT NOT NULL,
  publication_id INTEGER,
  draft_id TEXT,
  window_days INTEGER NOT NULL,
  window_start TEXT NOT NULL,
  due_at TEXT NOT NULL,
  window_end TEXT NOT NULL,
  captured_at TEXT,
  reads INTEGER,
  likes INTEGER,
  favorites INTEGER,
  comments INTEGER,
  impressions INTEGER,
  status TEXT NOT NULL,
  reason TEXT,
  source TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(subject_type, subject_key, window_days)
);
CREATE INDEX IF NOT EXISTS idx_perfwin_status ON performance_windows(status);
CREATE INDEX IF NOT EXISTS idx_perfwin_draft ON performance_windows(draft_id);
"""


# --------------------------------------------------------------------- 时间口径


def window_days() -> int:
    """T+N 的 N。环境变量优先于 config,非法值一律回落默认 3。"""
    raw = os.environ.get(WINDOW_DAYS_ENV)
    if raw not in (None, ""):
        try:
            return max(1, int(str(raw).strip()))
        except (TypeError, ValueError):
            pass
    try:
        conf = (cfg.load_config().get("metrics") or {}).get("performance_window_days")
        if conf is not None:
            return max(1, int(conf))
    except Exception:  # noqa: BLE001 - 配置缺失/损坏不阻断链路
        pass
    return DEFAULT_WINDOW_DAYS


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def _iso(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def window_bounds(published_at: str, days: int | None = None) -> dict[str, str | int]:
    """``published_at`` → T / due_at / window_end;发布时间不可解析时抛 ValueError。"""
    start = _parse_ts(published_at)
    if start is None:
        raise ValueError(f"发布时间无法解析:T 不可用,拒绝按 T+N 口径记账({published_at!r})")
    span = int(days if days is not None else window_days())
    due = start + timedelta(days=span)
    end = due + timedelta(hours=WINDOW_GRACE_HOURS)
    return {
        "window_days": span,
        "window_start": _iso(start),
        "due_at": _iso(due),
        "window_end": _iso(end),
    }


def classify(
    observations: list[dict],
    due_at: str,
    window_end: str,
    *,
    now: str | None = None,
) -> dict:
    """按 T+N 口径从观测里选一条读数。

    ``observations`` 需按 ``captured_at`` 升序;每条形如
    ``{"captured_at":..., "reads":..., ..., "data_source":...}``。
    返回 ``{status, captured_at, reads, ..., reason}``;没有可用读数时指标键一律为 None。
    """
    due = _parse_ts(due_at)
    end = _parse_ts(window_end)
    moment = _parse_ts(now or cfg.now_iso())
    if due is None or end is None or moment is None:
        raise ValueError("T+N 窗口时间不可解析")
    empty = {field: None for field in METRIC_FIELDS}

    if moment < due:
        if observations:
            return {
                **empty,
                "status": STATUS_EARLY,
                "captured_at": None,
                "reason": f"仅有 {len(observations)} 条早于 T+N 的观测,不作为到期读数",
            }
        return {**empty, "status": STATUS_PENDING, "captured_at": None, "reason": "T+N 窗口尚未到期"}

    if not observations:
        return {
            **empty,
            "status": STATUS_UNAVAILABLE,
            "captured_at": None,
            "reason": "没有任何观测,无法回填 T+N 读数",
        }

    in_window: list[dict] = []
    after: list[dict] = []
    for row in observations:
        captured = _parse_ts(row.get("captured_at"))
        if captured is None:
            continue
        if due <= captured <= end:
            in_window.append(row)
        elif captured > end:
            after.append(row)

    if in_window:
        row = in_window[0]
        return {
            **{field: row.get(field) for field in METRIC_FIELDS},
            "status": STATUS_ON_TIME,
            "captured_at": row.get("captured_at"),
            "reason": None,
        }
    if after:
        row = after[0]
        return {
            **{field: row.get(field) for field in METRIC_FIELDS},
            "status": STATUS_LATE,
            "captured_at": row.get("captured_at"),
            "reason": "窗口内无观测,取窗口后最早一条(口径不同,不可与 on_time 混用)",
        }
    return {
        **empty,
        "status": STATUS_OVERDUE,
        "captured_at": None,
        "reason": "T+N 窗口已过且没有任何到期后观测",
    }


# --------------------------------------------------------------------- 建表与写入


def ensure_schema(con) -> None:
    con.executescript(PUBLICATION_SCHEMA)


def _int_or_none(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    text = str(value).strip()
    if text == "" or text.lower() in {"null", "none", "na", "n/a", "-"}:
        return None
    try:
        number = int(float(text)) if "." in text or "e" in text.lower() else int(text)
    except ValueError:
        return None
    return number if number >= 0 else None


def bind(
    category: str,
    draft_id: str,
    url: str,
    published_at: str,
    *,
    version: int | None = None,
    platform: str | None = None,
    source: str = "declared",
    note: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """把一次发布动作绑到草稿上:草稿 → 链接 → 发布时间。

    ``published_at`` 不可解析时直接拒绝(不猜发布时间,否则 T+N 整条链失真)。
    """
    if not str(draft_id or "").strip():
        raise ValueError("draft_id 必填:没有草稿就谈不上回溯源")
    if not str(url or "").strip():
        raise ValueError("url 必填:发布链接是链路的锚点")
    normalized_time = timeparse.normalize_datetime(published_at)
    if not normalized_time:
        raise ValueError(f"published_at 无法解析为确定时间:{published_at!r}(T 不可用,拒绝绑定)")

    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    normalized_url = normalize_url(url)
    now = cfg.now_iso()
    con = connect(target)
    try:
        ensure_schema(con)
        con.execute(
            "INSERT INTO publications(category, draft_id, version, platform, url, url_normalized,"
            " published_at, published_at_source, bound_at, note) VALUES(?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(url_normalized) DO UPDATE SET category=excluded.category,"
            " draft_id=excluded.draft_id, version=excluded.version, platform=excluded.platform,"
            " url=excluded.url, published_at=excluded.published_at,"
            " published_at_source=excluded.published_at_source, bound_at=excluded.bound_at,"
            " note=excluded.note",
            (
                category,
                str(draft_id).strip(),
                version,
                platform,
                url,
                normalized_url,
                normalized_time,
                str(source or "declared"),
                now,
                note,
            ),
        )
        row = con.execute(
            "SELECT * FROM publications WHERE url_normalized=?", (normalized_url,)
        ).fetchone()
        result = refresh_publication_window(con, row, now=now)
        con.commit()
    finally:
        con.close()
    return result


def record_metrics(
    category: str,
    publication_id: int,
    metrics: dict,
    *,
    captured_at: str | None = None,
    data_source: str = "manual",
    note: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """记录一条发布维度观测,并重算该发布的 T+N 读数。

    规则与 ``metrics_import`` 一致:缺失字段存 NULL、不覆盖已知值、未授权来源拒绝。
    """
    source_id = str(data_source or "").strip()
    if not source_id:
        raise ValueError("data_source 必填:没有来源的指标无法溯源")
    from ttxt.analysis import metrics_import  # 延迟导入,避免模块级循环

    allowed = metrics_import.load_authorized_sources()
    if allowed and source_id not in allowed:
        raise PermissionError(
            f"未授权数据来源 {source_id!r}:不在 config.metrics.authorized_sources 白名单内"
        )
    values = {field: _int_or_none((metrics or {}).get(field)) for field in METRIC_FIELDS}
    if all(value is None for value in values.values()):
        raise ValueError("全部指标缺失,拒绝写入(缺失 ≠ 0,不能拿空记录充数)")

    if captured_at:
        captured = timeparse.normalize_datetime(captured_at)
        if not captured:
            raise ValueError(f"captured_at 无法解析:{captured_at!r}")
    else:
        captured = cfg.now_iso()

    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    now = cfg.now_iso()
    con = connect(target)
    try:
        ensure_schema(con)
        pub = con.execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
        if pub is None:
            raise LookupError(f"publication {publication_id} 不在 {category} 类目库")
        con.execute(
            "INSERT INTO publication_observations(publication_id, captured_at, reads, likes,"
            " favorites, comments, impressions, data_source, note) VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(publication_id, captured_at, data_source) DO UPDATE SET"
            " reads=COALESCE(excluded.reads, publication_observations.reads),"
            " likes=COALESCE(excluded.likes, publication_observations.likes),"
            " favorites=COALESCE(excluded.favorites, publication_observations.favorites),"
            " comments=COALESCE(excluded.comments, publication_observations.comments),"
            " impressions=COALESCE(excluded.impressions, publication_observations.impressions),"
            " note=COALESCE(excluded.note, publication_observations.note)",
            (
                publication_id,
                captured,
                values["reads"],
                values["likes"],
                values["favorites"],
                values["comments"],
                values["impressions"],
                source_id,
                note,
            ),
        )
        result = refresh_publication_window(con, pub, now=now)
        con.commit()
    finally:
        con.close()
    return result


def _upsert_window(con, payload: dict) -> None:
    con.execute(
        "INSERT INTO performance_windows(subject_type, subject_key, category, publication_id,"
        " draft_id, window_days, window_start, due_at, window_end, captured_at, reads, likes,"
        " favorites, comments, impressions, status, reason, source, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(subject_type, subject_key, window_days) DO UPDATE SET"
        " category=excluded.category, publication_id=excluded.publication_id,"
        " draft_id=excluded.draft_id, window_start=excluded.window_start, due_at=excluded.due_at,"
        " window_end=excluded.window_end, captured_at=excluded.captured_at, reads=excluded.reads,"
        " likes=excluded.likes, favorites=excluded.favorites, comments=excluded.comments,"
        " impressions=excluded.impressions, status=excluded.status, reason=excluded.reason,"
        " source=excluded.source, updated_at=excluded.updated_at",
        (
            payload["subject_type"],
            payload["subject_key"],
            payload["category"],
            payload.get("publication_id"),
            payload.get("draft_id"),
            payload["window_days"],
            payload["window_start"],
            payload["due_at"],
            payload["window_end"],
            payload.get("captured_at"),
            payload.get("reads"),
            payload.get("likes"),
            payload.get("favorites"),
            payload.get("comments"),
            payload.get("impressions"),
            payload["status"],
            payload.get("reason"),
            payload.get("source"),
            payload["updated_at"],
        ),
    )


def refresh_publication_window(con, pub, *, now: str | None = None, days: int | None = None) -> dict:
    """重算单个发布的 T+N 行并写回 ``performance_windows``。"""
    span = int(days) if days is not None else window_days()
    bounds = window_bounds(pub["published_at"], span)
    rows = con.execute(
        "SELECT captured_at, reads, likes, favorites, comments, impressions, data_source"
        " FROM publication_observations WHERE publication_id=? ORDER BY captured_at",
        (pub["id"],),
    ).fetchall()
    observations = [dict(row) for row in rows]
    picked = classify(observations, bounds["due_at"], bounds["window_end"], now=now)
    payload = {
        "subject_type": "publication",
        "subject_key": str(pub["id"]),
        "category": pub["category"],
        "publication_id": pub["id"],
        "draft_id": pub["draft_id"],
        **bounds,
        **picked,
        "source": observations[-1]["data_source"] if observations else None,
        "updated_at": now or cfg.now_iso(),
    }
    _upsert_window(con, payload)
    return {
        "publication_id": pub["id"],
        "draft_id": pub["draft_id"],
        "url": pub["url"],
        "published_at": pub["published_at"],
        **{key: payload[key] for key in ("window_days", "due_at", "window_end", "status", "reason")},
        **{field: picked[field] for field in METRIC_FIELDS},
        "missing_fields": [field for field in METRIC_FIELDS if picked[field] is None],
    }


def refresh_article_window(
    con, article_id: int, category: str, *, now: str | None = None, days: int | None = None
) -> dict | None:
    """重算**单篇文章**的 T+N 行(数据来自 ``metric_snapshots``),写回 ``performance_windows``。

    R42:每次采集写库后调用,保证队列排到的那一篇当场变成 on_time / late,
    不必等下一次全量回填。发布时间不可解析/文章不存在 ⇒ 返回 None(不编造窗口)。
    """
    span = int(days) if days is not None else window_days()
    row = con.execute(
        "SELECT id, url, published_at FROM articles WHERE id=?", (int(article_id),)
    ).fetchone()
    if row is None or not str(row["published_at"] or "").strip():
        return None
    try:
        bounds = window_bounds(row["published_at"], span)
    except ValueError:
        return None
    observations = [
        dict(item)
        for item in con.execute(
            "SELECT captured_at, reads, likes, favorites, comments, impressions, data_source"
            " FROM metric_snapshots WHERE article_id=? ORDER BY captured_at",
            (int(article_id),),
        ).fetchall()
    ]
    picked = classify(observations, bounds["due_at"], bounds["window_end"], now=now)
    _upsert_window(
        con,
        {
            "subject_type": "article",
            "subject_key": str(int(article_id)),
            "category": category,
            "publication_id": None,
            "draft_id": None,
            **bounds,
            **picked,
            "source": observations[-1]["data_source"] if observations else None,
            "updated_at": now or cfg.now_iso(),
        },
    )
    return {
        "article_id": int(article_id),
        "url": row["url"],
        "published_at": row["published_at"],
        **{key: bounds[key] for key in ("window_days", "due_at", "window_end")},
        **{key: picked[key] for key in ("status", "captured_at", "reason")},
        **{field: picked[field] for field in METRIC_FIELDS},
        "missing_fields": [field for field in METRIC_FIELDS if picked[field] is None],
    }


# --------------------------------------------------------------------- 历史回填


def backfill_articles(
    category: str,
    *,
    days: int | None = None,
    apply: bool = False,
    limit: int | None = None,
    db_path: Path | None = None,
) -> dict:
    """按文章已有 ``published_at`` + ``metric_snapshots`` 回填 T+N 读数。

    只使用可得信息:发布时间不可解析 ⇒ ``bad_published_at``;无快照 ⇒ ``unavailable``;
    快照时点不在窗口内 ⇒ ``early/late/overdue``。一条都不编。
    """
    span = int(days if days is not None else window_days())
    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    con = connect(target)
    status_counts = {status: 0 for status in ALL_STATUSES}
    missing_counts = {field: 0 for field in METRIC_FIELDS}
    written = 0
    try:
        ensure_schema(con)
        article_rows = con.execute(
            "SELECT id, published_at FROM articles WHERE published_at IS NOT NULL AND published_at<>''"
        ).fetchall()
        if limit:
            article_rows = article_rows[: int(limit)]
        published = {int(row["id"]): row["published_at"] for row in article_rows}
        total_articles = int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        snapshot_rows = con.execute(
            "SELECT article_id, captured_at, reads, likes, favorites, comments, impressions, data_source"
            " FROM metric_snapshots ORDER BY article_id, captured_at"
        ).fetchall()
        grouped: dict[int, list[dict]] = {}
        for row in snapshot_rows:
            article_id = int(row["article_id"])
            if article_id not in published:
                continue
            grouped.setdefault(article_id, []).append(dict(row))

        bad_time = 0
        now = cfg.now_iso()
        for article_id, published_at in published.items():
            try:
                bounds = window_bounds(published_at, span)
            except ValueError:
                bad_time += 1
                continue
            observations = grouped.get(article_id, [])
            picked = classify(observations, bounds["due_at"], bounds["window_end"], now=now)
            status_counts[picked["status"]] += 1
            for field in METRIC_FIELDS:
                if picked[field] is None:
                    missing_counts[field] += 1
            if apply:
                _upsert_window(
                    con,
                    {
                        "subject_type": "article",
                        "subject_key": str(article_id),
                        "category": category,
                        "publication_id": None,
                        "draft_id": None,
                        **bounds,
                        **picked,
                        "source": observations[-1]["data_source"] if observations else None,
                        "updated_at": now,
                    },
                )
                written += 1
        if apply:
            con.commit()
    finally:
        con.close()
    return {
        "category": category,
        "window_days": span,
        "grace_hours": WINDOW_GRACE_HOURS,
        "apply": apply,
        "articles_total": total_articles,
        "articles_with_published_at": len(published),
        "articles_without_published_at": total_articles - len(published),
        "bad_published_at": bad_time,
        "with_snapshots": sum(1 for article_id in published if grouped.get(article_id)),
        "status_counts": status_counts,
        "missing_counts": missing_counts,
        "windows_written": written,
    }


def backfill_all(categories: list[str], *, days: int | None = None, apply: bool = False) -> dict:
    results: dict[str, object] = {}
    for category in categories:
        try:
            results[category] = backfill_articles(category, days=days, apply=apply)
        except Exception as exc:  # noqa: BLE001 - 类目级错误隔离
            results[category] = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "apply": apply,
        "window_days": int(days if days is not None else window_days()),
        "categories": results,
    }


# --------------------------------------------------------------------- 查询与链路体检


def _window_dict(row) -> dict | None:
    if row is None or row["status"] is None:
        return None
    payload = {
        "window_days": row["window_days"],
        "due_at": row["due_at"],
        "window_end": row["window_end"],
        "captured_at": row["captured_at"],
        "status": row["status"],
        "reason": row["reason"],
        "source": row["source"],
    }
    for field in METRIC_FIELDS:
        payload[field] = row[field]
    payload["missing_fields"] = [field for field in METRIC_FIELDS if row[field] is None]
    return payload


def report(
    category: str,
    *,
    draft_id: str | None = None,
    limit: int | None = None,
    db_path: Path | None = None,
) -> dict:
    """按单篇查:草稿 → 发布链接/发布时间 → T+N 读数(缺失字段如实列出)。"""
    span = window_days()
    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    con = connect(target)
    try:
        ensure_schema(con)
        sql = (
            "SELECT p.id, p.draft_id, p.version, p.platform, p.url, p.published_at,"
            " p.published_at_source, p.bound_at, w.window_days, w.due_at, w.window_end,"
            " w.captured_at, w.reads, w.likes, w.favorites, w.comments, w.impressions,"
            " w.status, w.reason, w.source"
            " FROM publications p LEFT JOIN performance_windows w"
            " ON w.subject_type='publication' AND w.subject_key=CAST(p.id AS TEXT)"
            " AND w.window_days=?"
        )
        params: list[object] = [span]
        if draft_id:
            sql += " WHERE p.draft_id=?"
            params.append(draft_id)
        sql += " ORDER BY p.published_at DESC, p.id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    entries = []
    for row in rows:
        window = _window_dict(row) or {"status": STATUS_UNAVAILABLE, "missing_fields": list(METRIC_FIELDS)}
        entries.append(
            {
                "publication_id": row["id"],
                "draft_id": row["draft_id"],
                "version": row["version"],
                "platform": row["platform"],
                "url": row["url"],
                "published_at": row["published_at"],
                "published_at_source": row["published_at_source"],
                "bound_at": row["bound_at"],
                **window,
            }
        )
    return {"category": category, "window_days": span, "count": len(entries), "publications": entries}


def article_report(
    category: str,
    *,
    article_id: int | None = None,
    limit: int | None = 20,
    days: int | None = None,
    db_path: Path | None = None,
) -> dict:
    """按单篇文章查 T+N 读数(历史回填结果;链接与发布时间取自 articles)。"""
    span = int(days if days is not None else window_days())
    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    con = connect(target)
    try:
        ensure_schema(con)
        sql = (
            "SELECT a.id, a.title, a.url, a.published_at, w.window_days, w.due_at, w.window_end,"
            " w.captured_at, w.reads, w.likes, w.favorites, w.comments, w.impressions,"
            " w.status, w.reason, w.source"
            " FROM articles a JOIN performance_windows w"
            " ON w.subject_type='article' AND w.subject_key=CAST(a.id AS TEXT) AND w.window_days=?"
        )
        params: list[object] = [span]
        if article_id is not None:
            sql += " WHERE a.id=?"
            params.append(int(article_id))
        sql += (
            " ORDER BY CASE w.status WHEN 'on_time' THEN 0 WHEN 'late' THEN 1"
            " WHEN 'pending' THEN 2 WHEN 'early' THEN 3 WHEN 'overdue' THEN 4 ELSE 5 END,"
            " a.published_at DESC, a.id DESC"
        )
        if limit and article_id is None:
            sql += f" LIMIT {int(limit)}"
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    entries = []
    for row in rows:
        window = _window_dict(row) or {"status": STATUS_UNAVAILABLE, "missing_fields": list(METRIC_FIELDS)}
        entries.append(
            {
                "article_id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "published_at": row["published_at"],
                **window,
            }
        )
    return {"category": category, "window_days": span, "count": len(entries), "articles": entries}


def chain_usability(category: str, *, days: int | None = None, db_path: Path | None = None) -> dict:
    """链路可用性实测:能绑定多少、断在哪一步、缺什么字段。"""
    span = int(days if days is not None else window_days())
    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    con = connect(target)
    try:
        ensure_schema(con)
        drafts_total = int(
            con.execute("SELECT COUNT(DISTINCT draft_id) FROM writing_history").fetchone()[0]
        )
        publications = int(con.execute("SELECT COUNT(*) FROM publications").fetchone()[0])
        drafts_bound = int(
            con.execute(
                "SELECT COUNT(DISTINCT draft_id) FROM publications WHERE TRIM(COALESCE(draft_id,''))<>''"
            ).fetchone()[0]
        )
        observations = int(
            con.execute("SELECT COUNT(*) FROM publication_observations").fetchone()[0]
        )
        articles_total = int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        articles_with_pub = int(
            con.execute(
                "SELECT COUNT(*) FROM articles WHERE published_at IS NOT NULL AND published_at<>''"
            ).fetchone()[0]
        )
        snapshots = int(con.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0])
        article_windows = int(
            con.execute(
                "SELECT COUNT(*) FROM performance_windows WHERE subject_type='article' AND window_days=?",
                (span,),
            ).fetchone()[0]
        )
        window_rows = con.execute(
            "SELECT status, COUNT(*) AS n,"
            " SUM(CASE WHEN captured_at IS NOT NULL THEN 1 ELSE 0 END) AS readings,"
            " SUM(CASE WHEN captured_at IS NOT NULL AND reads IS NULL THEN 1 ELSE 0 END) AS reads_null,"
            " SUM(CASE WHEN captured_at IS NOT NULL AND likes IS NULL THEN 1 ELSE 0 END) AS likes_null,"
            " SUM(CASE WHEN captured_at IS NOT NULL AND favorites IS NULL THEN 1 ELSE 0 END) AS favorites_null,"
            " SUM(CASE WHEN captured_at IS NOT NULL AND comments IS NULL THEN 1 ELSE 0 END) AS comments_null,"
            " SUM(CASE WHEN captured_at IS NOT NULL AND impressions IS NULL THEN 1 ELSE 0 END) AS impressions_null"
            " FROM performance_windows WHERE window_days=? GROUP BY status",
            (span,),
        ).fetchall()
    finally:
        con.close()

    status_totals = {status: 0 for status in ALL_STATUSES}
    null_totals = {field: 0 for field in METRIC_FIELDS}
    readings_total = 0
    for row in window_rows:
        status_totals[row["status"]] = int(row["n"])
        readings_total += int(row["readings"] or 0)
        for field in METRIC_FIELDS:
            null_totals[field] += int(row[f"{field}_null"] or 0)
    windows_total = sum(status_totals.values())

    draft_step = {
        "step": "draft → publication",
        "total": drafts_total,
        "bound": drafts_bound,
        "status": (
            "empty"
            if not drafts_total
            else "ok"
            if drafts_bound == drafts_total
            else ("broken" if drafts_bound == 0 else "partial")
        ),
        "missing_fields": [] if drafts_bound else ["draft url", "published_at", "platform article id"],
        "detail": (
            "writing_history 只有草稿正文/模型/时间,没有发布链接、发布时间或平台文章 id;"
            "在不猜测的前提下无法自动绑定。绑定必须由发布动作调用 publish bind 写入。"
            if drafts_bound == 0
            else "已有发布记录,断点见未绑定草稿"
        ),
    }
    metric_step = {
        "step": "publication → observations",
        "total": publications,
        "bound": observations,
        "status": (
            "empty"
            if not publications
            else "ok"
            if observations
            else "broken"
        ),
        "missing_fields": (
            sorted(field for field, n in null_totals.items() if n) if observations else []
        ),
        "detail": "观测来自 publish record(或后续采集写入);未记录前 T+N 一律 unavailable。",
    }
    article_step = {
        "step": "article → T+N snapshot",
        "total": articles_with_pub,
        "bound": article_windows,
        "status": "ok" if windows_total and status_totals[STATUS_ON_TIME] else ("partial" if article_windows else "broken"),
        "missing_fields": sorted(field for field, n in null_totals.items() if n),
        "detail": (
            "历史文章按 published_at 回填;metric_snapshots 是唯一时序来源,"
            "快照时点与发布时间无绑定关系,只能按时间窗判定 on_time/late/early/overdue。"
        ),
    }
    return {
        "category": category,
        "window_days": span,
        "grace_hours": WINDOW_GRACE_HOURS,
        "counts": {
            "drafts_total": drafts_total,
            "drafts_bound": drafts_bound,
            "publications": publications,
            "publication_observations": observations,
            "articles_total": articles_total,
            "articles_with_published_at": articles_with_pub,
            "metric_snapshots": snapshots,
            "performance_windows": windows_total,
            "readings_total": readings_total,
        },
        "status_counts": status_totals,
        "missing_counts": null_totals,  # 只统计「有读数」的窗口,未到期不算缺失
        "breakpoints": [draft_step, metric_step, article_step],
    }
