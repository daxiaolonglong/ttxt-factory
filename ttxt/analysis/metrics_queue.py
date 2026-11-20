"""R42:表现采集按 ``due_at`` 排队 —— 到点必采,不再「碰巧跑到就采」。

为什么必须新增一张队列表(而不是复用现有结构):
- ``article_metrics`` 只存「当前值」,``articles_meta`` 是 kv,两者都无法表达
  「这篇文章应当在哪一刻采 / 已试几次 / 下次何时再试」;
- 用现有结构就得把状态编进字符串或每轮扫全库重算,既不可审计,也挡不住重试风暴。
因此只在类目库内加 ``metrics_due_queue``,**不改 articles / article_metrics / metric_snapshots 任何字段**。

口径(本轮定死,不改评分、不动门禁):
- 只收有 ``published_at`` 的文章;T=``published_at``,``due_at=T+N``,窗口 ``[due_at, due_at+24h]``;
- ``now < due_at`` ⇒ ``scheduled``(到点自动转 ready);到期前的 early 观测**不能**抵掉到期读数;
- ``due_at <= now <= window_end`` ⇒ ``ready``(窗口内还没拿到读数 —— 这是还能补成 on_time 的部分);
- ``now > window_end`` 且没有任何读数 ⇒ ``backlog``(历史积压,按窗口结束时间由近到远补);
- 窗口内取**最早**一条的规则保持不变,由 ``publication.classify`` 执行,本模块只排队、不改口径;
- 成功一次即 ``done``:窗口内最早的一条已经拿到,再采也不可能更早;
- 失败按 1h / 6h / 24h 退避,每篇最多 3 次;404/410 视为终结(已由下架台账接管);
- dry-run 不推进 attempts、不写任何状态,避免「试跑到把重试次数烧光」;
- ``favorites`` 没有取证就永远不写,采集器恒为 NULL,本模块也不做任何填补。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ttxt import config as cfg
from ttxt.db import connect
from ttxt.logging import get_logger
from ttxt.writing import publication

LOG = get_logger("metrics_queue")

# 重采/重试口径:窗口内成功一次即停;失败按下面的间隔重试,最多 MAX_ATTEMPTS 次。
# 这与「窗口内取最早」一致:成功后更晚的观测永远不会成为 T+N 读数,重复采没有意义。
MAX_ATTEMPTS = 3
RETRY_BACKOFF_HOURS = (1.0, 6.0, 24.0)

STATE_SCHEDULED = "scheduled"   # 未到期,next_attempt_at=due_at
STATE_READY = "ready"           # 已到期、窗口内,等采
STATE_BACKLOG = "backlog"       # 窗口已过、无读数,历史积压
STATE_DONE = "done"             # 已拿到 T+N 读数(on_time / late),不再采
STATE_EXHAUSTED = "exhausted"   # 重试上限用尽,停止尝试
ALL_STATES = (STATE_SCHEDULED, STATE_READY, STATE_BACKLOG, STATE_DONE, STATE_EXHAUSTED)

# 采集器不会对白名单外的域名发请求;这类文章重试多少次都不会成功 ⇒ 直接终结。
OUTCOME_UNSUPPORTED_HOST = "unsupported_host"

# R58(2026-11-20,项目负责人裁决):基础设施故障(代理/网络不可达)与内容级失败
# (404、无指标)不是一类问题。实测:2026-11-20 17:37:08→17:38:29 一次 81 秒的代理抖动
# (``ERR_PROXY_CONNECTION_FAILED``)把 58 条推到 attempts≥2、1 条转 exhausted —— 按
# 1h/6h/24h 退避反复重试只会把"12 条/轮"的额度烧在故障期里。因此这类失败:
# 不计入重试上限、按 INFRA_COOLDOWN_HOURS 冷却后一次性重试,冷却窗口内整条链路暂停并报出。
OUTCOME_INFRA = "infra_unavailable"
INFRA_PATTERNS = (
    "ERR_PROXY",
    "ERR_TUNNEL",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_ADDRESS_UNREACHABLE",
    "ERR_SOCKET_NOT_CONNECTED",
    "CONNECTION_FAILED",
    "NETWORK_UNREACHABLE",
)
INFRA_COOLDOWN_HOURS = 6.0
NON_TERMINAL_STATES = (STATE_SCHEDULED, STATE_READY, STATE_BACKLOG)


def is_infra_failure(reason: str | None) -> bool:
    """判定是否基础设施故障(代理/网络),而不是内容级失败(404、无指标、下架)。"""
    text = str(reason or "").upper()
    return any(pattern in text for pattern in INFRA_PATTERNS)


def _url_of(row) -> str:
    return str((row["url"] if "url" in row.keys() else "") or "")


QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics_due_queue (
    category TEXT NOT NULL,
    article_id INTEGER NOT NULL,
    published_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    window_end TEXT NOT NULL,
    window_days INTEGER NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_attempt_at TEXT,
    last_outcome TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (category, article_id)
);
CREATE INDEX IF NOT EXISTS idx_mdq_claim
    ON metrics_due_queue(category, state, next_attempt_at, due_at);
"""


def _parse_ts(value: str | None) -> datetime | None:
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


def _target(category: str, db_path: Path | None) -> Path:
    return Path(db_path) if db_path is not None else cfg.category_db_path(category)


def ensure_schema(con) -> None:
    con.executescript(QUEUE_SCHEMA)


def _keep_backoff(prev: dict | None, moment: datetime) -> str | None:
    """退避时间仍在未来就保留,否则立即到期。"""
    if not prev:
        return None
    raw = prev.get("next_attempt_at")
    due = _parse_ts(raw)
    if due is not None and due > moment:
        return raw
    return None


def sync_queue(
    category: str,
    *,
    days: int | None = None,
    now: str | None = None,
    db_path: Path | None = None,
    include_new_articles: bool = False,
    gate_hosts: bool = True,
    backfill_limit: int | None = None,
) -> dict:
    """按 ``performance_windows`` 重算队列状态(幂等,保留 attempts / 退避时间)。

    返回各状态条数;``removed`` 是窗口已不存在(换口径/文章消失)而被清掉的队列行。

    R58(2026-11-20,项目负责人裁决)两处口径变更:

    - ``include_new_articles``:先把"还没有窗口行"的文章补出窗口行再入队。窗口行原先只有
      ``ttxt publish backfill`` 会写,调度器不写 ⇒ 新文章永远排不进队列(实测 technology
      1014 篇里 546 篇从未入队,窗口行最后更新停在 2026-11-19T17:21)。补行按 ``only_missing``
      + 唯一键 ⇒ 跑两次候选集为空,幂等;``backfill_limit`` 限制单轮补行数(先近后远)。
    - ``gate_hosts``:域名不在公开指标白名单的文章**不入队**;已经排进去的按 ``unsupported_host``
      归档(``archive_unsupported_hosts``),不重试。原先白名单只在采集回执里判,实测 176 条
      非白名单域被排队、77 条记 done 却零快照,白烧每轮 12 条的额度。
    """
    from ttxt.crawler import metrics_pages

    span = int(days) if days is not None else publication.window_days()
    moment = _parse_ts(now) if now else datetime.now().astimezone()
    if moment is None:
        moment = datetime.now().astimezone()
    now_iso = _iso(moment)
    target = _target(category, db_path)
    gone_ids = _fresh_gone_ids(category, db_path, moment)
    backfill: dict[str, Any] | None = None
    if include_new_articles:
        backfill = publication.backfill_articles(
            category,
            days=span,
            apply=True,
            db_path=db_path,
            only_missing=True,
            limit=backfill_limit,
            host_gate=gate_hosts,
        )
        LOG.info(
            "metrics_due.window_backfill",
            "入队前补窗口行:白名单外的文章连窗口行都不补",
            category=category,
            missing_windows_in_whitelist=backfill.get("missing_windows_total"),
            windows_inserted=int(backfill.get("windows_inserted") or 0),
            host_gate_skipped=backfill.get("host_gate_skipped"),
        )
    con = connect(target)
    try:
        publication.ensure_schema(con)
        ensure_schema(con)
        rows = con.execute(
            "SELECT w.subject_key, w.due_at, w.window_end, w.status, a.published_at,"
            " COALESCE(a.url_normalized, a.url, '') AS url"
            " FROM performance_windows w JOIN articles a ON a.id = CAST(w.subject_key AS INTEGER)"
            " WHERE w.subject_type='article' AND w.window_days=?",
            (span,),
        ).fetchall()
        prev_rows = {
            int(item["article_id"]): dict(item)
            for item in con.execute(
                "SELECT * FROM metrics_due_queue WHERE category=?", (category,)
            ).fetchall()
        }
        counts: Counter[str] = Counter()
        upserted = 0
        host_skipped = 0
        for row in rows:
            article_id = int(row["subject_key"])
            due = _parse_ts(row["due_at"])
            end = _parse_ts(row["window_end"])
            if due is None or end is None:
                continue
            if gate_hosts and not metrics_pages.host_allowed(_url_of(row)):
                # 白名单外的域名采集器永远不会发请求 ⇒ 不入队、不占每轮额度;
                # 已经在队里的非终结条目由 _archive_unsupported_on 一并归档。
                host_skipped += 1
                continue
            status = str(row["status"] or "")
            prev = prev_rows.get(article_id)
            prev_state = str(prev["state"]) if prev else ""
            attempts = int(prev["attempts"]) if prev else 0

            if prev_state == STATE_DONE or status in (publication.STATUS_ON_TIME, publication.STATUS_LATE):
                state, next_at = STATE_DONE, None
            elif article_id in gone_ids:
                # 下架台账(404/410)仍在有效期:不再发请求,直接终结
                state, next_at = STATE_DONE, None
            elif prev_state == STATE_EXHAUSTED and moment > end:
                # 上限已用尽:同一窗口内不重开,免得每轮都去撞同一堵墙
                state, next_at = STATE_EXHAUSTED, None
            elif moment < due:
                state, next_at = STATE_SCHEDULED, row["due_at"]
            elif moment <= end:
                state = STATE_READY
                next_at = _keep_backoff(prev, moment) or now_iso
            else:
                state = STATE_BACKLOG
                next_at = _keep_backoff(prev, moment) or now_iso

            con.execute(
                "INSERT INTO metrics_due_queue(category, article_id, published_at, due_at,"
                " window_end, window_days, state, attempts, next_attempt_at, last_attempt_at,"
                " last_outcome, last_error, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(category, article_id) DO UPDATE SET"
                " published_at=excluded.published_at, due_at=excluded.due_at,"
                " window_end=excluded.window_end, window_days=excluded.window_days,"
                " state=excluded.state, next_attempt_at=excluded.next_attempt_at,"
                " updated_at=excluded.updated_at",
                (
                    category,
                    article_id,
                    str(row["published_at"] or ""),
                    row["due_at"],
                    row["window_end"],
                    span,
                    state,
                    attempts,
                    next_at,
                    prev.get("last_attempt_at") if prev else None,
                    prev.get("last_outcome") if prev else None,
                    prev.get("last_error") if prev else None,
                    now_iso,
                ),
            )
            counts[state] += 1
            upserted += 1

        current_ids = {int(row["subject_key"]) for row in rows}
        stale = [aid for aid in prev_rows if aid not in current_ids]
        for article_id in stale:
            con.execute(
                "DELETE FROM metrics_due_queue WHERE category=? AND article_id=?",
                (category, article_id),
            )
        archived = _archive_unsupported_on(con, category, now_iso) if gate_hosts else {}
        con.commit()
    finally:
        con.close()
    return {
        "category": category,
        "window_days": span,
        "now": now_iso,
        "total": upserted,
        "states": {state: counts.get(state, 0) for state in ALL_STATES},
        "removed": len(stale),
        "host_skipped": host_skipped,
        "archived_unsupported": archived,
        "window_backfill": backfill,
    }


def _archive_unsupported_on(con, category: str, now_iso: str) -> dict:
    """把队列里域名不在白名单的非终结条目按 ``unsupported_host`` 归档(不重试)。

    单独成函数是为了能被测试直接钉住:这些条目重试多少次都采不到,留在队列里
    只会每轮占掉"12 条/轮"的额度(实测 technology 176 条非白名单域排队、77 条记 done
    却零快照)。归档 = ``exhausted`` + attempts 用满 + ``next_attempt_at=NULL``,
    ``claim_batch`` 之后不会再取到它们。
    """
    from ttxt.crawler import metrics_pages

    rows = con.execute(
        "SELECT q.article_id, q.state, COALESCE(a.url_normalized, a.url, '') AS url"
        " FROM metrics_due_queue q LEFT JOIN articles a ON a.id = q.article_id"
        " WHERE q.category=? AND q.state IN (?,?,?)",
        (category, *NON_TERMINAL_STATES),
    ).fetchall()
    archived: Counter[str] = Counter()
    for item in rows:
        if metrics_pages.host_allowed(_url_of(item)):
            continue
        con.execute(
            "UPDATE metrics_due_queue SET state=?, attempts=?, next_attempt_at=NULL,"
            " last_outcome=?, last_error=?, updated_at=?"
            " WHERE category=? AND article_id=?",
            (
                STATE_EXHAUSTED,
                MAX_ATTEMPTS,
                OUTCOME_UNSUPPORTED_HOST,
                "域名不在公开白名单,采集器不发请求(R58:白名单校验前置到入队之前)",
                now_iso,
                category,
                int(item["article_id"]),
            ),
        )
        archived[str(item["state"])] += 1
    return dict(archived)


def archive_unsupported_hosts(
    category: str,
    *,
    now: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """对外入口:单独跑一次归档(同步/报表用),口径与 ``sync_queue`` 内的归档一致。"""
    moment = _parse_ts(now) if now else datetime.now().astimezone()
    if moment is None:
        moment = datetime.now().astimezone()
    con = connect(_target(category, db_path))
    try:
        ensure_schema(con)
        archived = _archive_unsupported_on(con, category, _iso(moment))
        con.commit()
    finally:
        con.close()
    return {"category": category, "archived_by_previous_state": archived, "total": sum(archived.values())}


def infra_state(category: str, *, now: str | None = None, db_path: Path | None = None) -> dict:
    """代理/网络故障的分层读数:最近一次基础设施故障之后、冷却窗口内 ⇒ 整条链路暂停。

    R58 裁决:``ERR_PROXY_CONNECTION_FAILED`` 这类故障与 404 不是一类问题,不该按
    1h/6h/24h 退避反复重试消耗"12 条/轮"的额度。判定口径(只看库,不发请求):

    - ``last_infra_at`` = 该类目队列行里最近一次 ``last_outcome=infra_unavailable`` 的**尝试时间**
      (``last_attempt_at``,不会被每轮 sync 刷新 —— 用 ``updated_at`` 会让冷却窗口每轮顺延、
      永远醒不过来);
    - ``last_success_at`` = 最近一次成功回执(``outcomes`` 为 ok/gone)的尝试时间;
    - 若 ``last_infra_at`` 比最后一次成功更晚,且 ``now < last_infra_at + INFRA_COOLDOWN_HOURS``
      ⇒ ``paused=True``:本轮一个请求都不发,回执里报 ``infra_paused``,由项目负责人处置
      (修好后下一轮自动恢复,修不好不会带着故障空转)。
    """
    moment = _parse_ts(now) if now else datetime.now().astimezone()
    if moment is None:
        moment = datetime.now().astimezone()
    con = connect(_target(category, db_path))
    try:
        ensure_schema(con)
        last_infra = con.execute(
            "SELECT MAX(last_attempt_at) FROM metrics_due_queue"
            " WHERE category=? AND last_outcome=?",
            (category, OUTCOME_INFRA),
        ).fetchone()[0]
        pending = int(
            con.execute(
                "SELECT COUNT(*) FROM metrics_due_queue"
                " WHERE category=? AND last_outcome=? AND state IN (?,?,?)",
                (category, OUTCOME_INFRA, *NON_TERMINAL_STATES),
            ).fetchone()[0]
        )
        last_success = con.execute(
            "SELECT MAX(last_attempt_at) FROM metrics_due_queue"
            " WHERE category=? AND state=? AND last_attempt_at IS NOT NULL",
            (category, STATE_DONE),
        ).fetchone()[0]
    finally:
        con.close()

    infra_at = _parse_ts(last_infra)
    next_retry = _iso(infra_at + timedelta(hours=INFRA_COOLDOWN_HOURS)) if infra_at else None
    success_at = _parse_ts(last_success)
    paused = bool(
        infra_at is not None
        and (success_at is None or infra_at >= success_at)
        and moment < infra_at + timedelta(hours=INFRA_COOLDOWN_HOURS)
    )
    return {
        "category": category,
        "now": _iso(moment),
        "last_infra_at": last_infra,
        "last_success_at": last_success,
        "infra_pending": pending,
        "cooldown_hours": INFRA_COOLDOWN_HOURS,
        "next_retry_at": next_retry,
        "paused": paused,
    }


def _fresh_gone_ids(category: str, db_path: Path | None, now: datetime) -> set[int]:
    """下架台账里仍在有效期内的文章:不再发请求(由 metrics_pages 的台账接管)。"""
    from ttxt.crawler import metrics_pages

    return set(metrics_pages.fresh_gone_ids(category, db_path=db_path, now=now))


def claim_batch(
    category: str,
    *,
    limit: int = 20,
    now: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """取本轮该采的批次。

    优先级:窗口内(ready)先于历史积压(backlog);组内 ready 按 ``due_at`` 升序(最早到期先采),
    backlog 按 ``window_end`` 倒序(离现在最近的积压先补,内容更可能还在)。
    """
    moment = _parse_ts(now) if now else datetime.now().astimezone()
    if moment is None:
        moment = datetime.now().astimezone()
    now_iso = _iso(moment)
    target = _target(category, db_path)
    con = connect(target)
    try:
        ensure_schema(con)
        gone = _fresh_gone_ids(category, db_path, moment)
        rows = con.execute(
            "SELECT article_id, state, due_at, window_end, attempts FROM metrics_due_queue"
            " WHERE category=? AND state IN (?,?) AND attempts < ?"
            " AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
            " ORDER BY CASE state WHEN ? THEN 0 ELSE 1 END,"
            " CASE WHEN state=? THEN due_at END ASC,"
            " CASE WHEN state=? THEN window_end END DESC, article_id ASC",
            (
                category,
                STATE_READY,
                STATE_BACKLOG,
                MAX_ATTEMPTS,
                now_iso,
                STATE_READY,
                STATE_READY,
                STATE_BACKLOG,
            ),
        ).fetchall()
    finally:
        con.close()
    picked = [dict(row) for row in rows if int(row["article_id"]) not in gone]
    capped = picked[: max(0, int(limit))] if limit is not None else picked
    return {
        "category": category,
        "now": now_iso,
        "eligible": len(picked),
        "gone_skipped": len(rows) - len(picked),
        "batch": capped,
        "article_ids": [int(row["article_id"]) for row in capped],
    }


def record_attempts(
    category: str,
    results: list[dict],
    *,
    now: str | None = None,
    db_path: Path | None = None,
    apply: bool = True,
) -> dict:
    """把一轮逐篇结果写回队列:成功→done;失败按退避排下一次;到上限→exhausted。

    ``apply=False`` 只返回计划,不推进 attempts(干跑不能烧掉重试次数)。
    """
    moment = _parse_ts(now) if now else datetime.now().astimezone()
    if moment is None:
        moment = datetime.now().astimezone()
    now_iso = _iso(moment)

    from ttxt.crawler import metrics_pages

    ok_outcomes = {metrics_pages.OUTCOME_OK, metrics_pages.OUTCOME_GONE, OUTCOME_UNSUPPORTED_HOST}
    target = _target(category, db_path)
    plan: list[dict] = []
    updated = 0
    con = connect(target)
    try:
        ensure_schema(con)
        for entry in results:
            article_id = int(entry["article_id"])
            outcome = str(entry.get("outcome") or "")
            row = con.execute(
                "SELECT state, attempts FROM metrics_due_queue WHERE category=? AND article_id=?",
                (category, article_id),
            ).fetchone()
            item = {
                "article_id": article_id,
                "outcome": outcome,
                "previous_state": None,
                "attempts": None,
                "state": None,
                "next_attempt_at": None,
            }
            plan.append(item)
            if row is None:
                item["state"] = "missing"
                continue
            state = str(row["state"])
            attempts = int(row["attempts"] or 0) + 1
            item["previous_state"] = state
            item["attempts"] = attempts
            if is_infra_failure(entry.get("reason")):
                # R58:代理/网络故障不是这篇文章的问题 —— 不计入重试上限(否则一次代理抖动
                # 就能把整批推到 exhausted),改为冷却 INFRA_COOLDOWN_HOURS 后一次性重试;
                # 冷却窗口内 run_due 会整条链路暂停(见 infra_state),不带故障空转。
                attempts = int(row["attempts"] or 0)
                item["attempts"] = attempts
                item["outcome"] = OUTCOME_INFRA
                state = state if state in (STATE_READY, STATE_BACKLOG) else STATE_READY
                next_at = _iso(moment + timedelta(hours=INFRA_COOLDOWN_HOURS))
                item["state"] = state
                item["next_attempt_at"] = next_at
                if not apply:
                    continue
                con.execute(
                    "UPDATE metrics_due_queue SET state=?, attempts=?, next_attempt_at=?,"
                    " last_attempt_at=?, last_outcome=?, last_error=?, updated_at=?"
                    " WHERE category=? AND article_id=?",
                    (
                        state,
                        attempts,
                        next_at,
                        now_iso,
                        OUTCOME_INFRA,
                        (str(entry.get("reason") or "")[:200] or None),
                        now_iso,
                        category,
                        article_id,
                    ),
                )
                updated += 1
                continue
            if outcome in ok_outcomes:
                state, next_at = STATE_DONE, None
            elif attempts >= MAX_ATTEMPTS:
                state, next_at = STATE_EXHAUSTED, None
            else:
                hours = RETRY_BACKOFF_HOURS[min(attempts, len(RETRY_BACKOFF_HOURS)) - 1]
                next_at = _iso(moment + timedelta(hours=float(hours)))
                if state not in (STATE_READY, STATE_BACKLOG):
                    state = STATE_READY
            item["state"] = state
            item["next_attempt_at"] = next_at
            if not apply:
                # 干跑只给计划,不推进 attempts、不写任何状态
                continue
            con.execute(
                "UPDATE metrics_due_queue SET state=?, attempts=?, next_attempt_at=?,"
                " last_attempt_at=?, last_outcome=?, last_error=?, updated_at=?"
                " WHERE category=? AND article_id=?",
                (
                    state,
                    attempts,
                    next_at,
                    now_iso,
                    str(entry.get("outcome") or ""),
                    (str(entry.get("reason") or "")[:200] or None),
                    now_iso,
                    category,
                    int(entry["article_id"]),
                ),
            )
            updated += 1
        con.commit()
    finally:
        con.close()
    return {"category": category, "apply": bool(apply), "planned": plan, "updated": updated}


def _unsupported_host_ids(category: str, article_ids: list[int], db_path: Path | None) -> list[int]:
    """批次里域名不在公开白名单的文章 —— 采集器根本不会发请求,重试也无意义。"""
    if not article_ids:
        return []
    from ttxt.crawler import metrics_pages

    target = _target(category, db_path)
    con = connect(target)
    try:
        marks = ",".join("?" for _ in article_ids)
        rows = con.execute(
            f"SELECT id, url, url_normalized FROM articles WHERE id IN ({marks})",
            [int(item) for item in article_ids],
        ).fetchall()
    finally:
        con.close()
    return [
        int(row["id"])
        for row in rows
        if not metrics_pages.host_allowed(str(row["url_normalized"] or row["url"] or ""))
    ]


def run_due(
    category: str,
    *,
    limit: int = 20,
    apply: bool = False,
    days: int | None = None,
    now: str | None = None,
    db_path: Path | None = None,
    collector: Callable[..., dict] | None = None,
    collect_kwargs: dict | None = None,
    include_new_articles: bool = False,
    gate_hosts: bool = True,
    backfill_limit: int | None = None,
) -> dict:
    """同步队列 → 取到期批次 → 采集 → (apply 时)记 attempts + 刷新 T+N 窗口。

    ``collector`` 默认 ``metrics_pages.collect_with_browser``(与现有采集同一套
    robots / 白名单 / 下架台账 / favorites 恒 NULL 保护),测试可注入假采集器。

    R58:``include_new_articles`` 只在建真跑(``apply``)时补窗口行(dry-run 不写库);
    ``gate_hosts`` 把白名单校验前置到入队之前;冷却窗口内若最近一次回执是代理/网络故障,
    本轮直接 ``skipped=infra_paused``,不开浏览器、不发请求。
    """
    from ttxt.crawler import metrics_pages

    span = int(days) if days is not None else publication.window_days()
    sync = sync_queue(
        category,
        days=span,
        now=now,
        db_path=db_path,
        include_new_articles=bool(include_new_articles and apply),
        gate_hosts=gate_hosts,
        backfill_limit=backfill_limit,
    )
    guard = infra_state(category, now=now, db_path=db_path)
    report: dict[str, Any] = {
        "category": category,
        "window_days": span,
        "apply": bool(apply),
        "queue_sync": sync,
        "infra": guard,
        "skipped": None,
        "eligible": 0,
        "claimed": 0,
        "claimed_ids": [],
        "gone_skipped": 0,
        "collection": None,
        "attempts": None,
        "unsupported_host_skipped": 0,
        "windows_refreshed": 0,
        "infra_failures": 0,
        "infra_down": False,
        "after": None,
    }
    if guard["paused"]:
        report["skipped"] = "infra_paused"
        report["after"] = timing_report(category, days=span, now=now, db_path=db_path)
        LOG.warn(
            "metrics_due.skipped_infra_paused",
            "指标回采链路暂停:最近一次回执是基础设施故障,冷却窗口内一个请求都不发",
            category=category,
            last_infra_at=guard["last_infra_at"],
            cooldown_hours=INFRA_COOLDOWN_HOURS,
            next_retry_at=guard["next_retry_at"],
            infra_pending=guard["infra_pending"],
        )
        return report
    claimed = claim_batch(category, limit=limit, now=now, db_path=db_path)
    article_ids = claimed["article_ids"]
    report["eligible"] = claimed["eligible"]
    report["claimed"] = len(article_ids)
    report["claimed_ids"] = article_ids
    report["gone_skipped"] = claimed["gone_skipped"]
    if not article_ids:
        report["after"] = timing_report(category, days=span, now=now, db_path=db_path)
        return report

    kwargs = dict(collect_kwargs or {})
    kwargs.setdefault("limit", None)  # ids 已定,不再按 limit 截断
    kwargs.setdefault("apply", apply)
    kwargs.setdefault("article_ids", article_ids)
    run = collector or metrics_pages.collect_with_browser
    result = run(category, db_path=db_path, **kwargs)
    report["collection"] = {
        key: result.get(key)
        for key in (
            "candidates",
            "fetched",
            "written",
            "updated",
            "snapshots",
            "would_write",
            "robots_refused",
            "fetch_errors",
            "no_metrics_skipped",
            "known_gone_skipped",
            "outcome_counts",
        )
    }
    if apply:
        outcomes = [
            {
                "article_id": int(item["article_id"]),
                "outcome": item.get("outcome"),
                "reason": item.get("reason"),
            }
            for item in result.get("outcomes", [])
        ]
        answered = {item["article_id"] for item in outcomes}
        unsupported = [
            article_id
            for article_id in _unsupported_host_ids(category, article_ids, db_path)
            if article_id not in answered
        ]
        outcomes.extend(
            {
                "article_id": article_id,
                "outcome": OUTCOME_UNSUPPORTED_HOST,
                "reason": "域名不在公开白名单,采集器不发请求",
            }
            for article_id in unsupported
        )
        report["unsupported_host_skipped"] = len(unsupported)
        report["attempts"] = record_attempts(
            category, outcomes, now=now, db_path=db_path, apply=True
        )
        # 基础设施故障单独分层:不计重试上限,且下一轮 run_due 会在冷却窗口内整条链路暂停。
        infra_hits = [item for item in outcomes if is_infra_failure(item.get("reason"))]
        report["infra_failures"] = len(infra_hits)
        report["infra_down"] = bool(infra_hits)
        report["infra"] = infra_state(category, now=now, db_path=db_path)
        if infra_hits:
            LOG.error(
                "metrics_due.infra_failure",
                "指标回采出现基础设施故障:这批不按文章级退避消耗额度,下一轮冷却窗口内整条链路暂停",
                category=category,
                count=len(infra_hits),
                sample_reason=str(infra_hits[0].get("reason") or "")[:120],
                next_retry_at=report["infra"]["next_retry_at"],
                hint="仍故障则保持暂停并报项目负责人,不要带故障空转",
            )
        ok_ids = [
            int(item["article_id"])
            for item in result.get("outcomes", [])
            if item.get("outcome") == metrics_pages.OUTCOME_OK
        ]
        target = _target(category, db_path)
        con = connect(target)
        try:
            for article_id in ok_ids:
                if publication.refresh_article_window(
                    con, article_id, category, now=now, days=span
                ):
                    report["windows_refreshed"] += 1
            con.commit()
        finally:
            con.close()
    report["after"] = timing_report(category, days=span, now=now, db_path=db_path)
    return report


def timing_report(
    category: str,
    *,
    days: int | None = None,
    now: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """取数时点口径的实测读数:晚采比例、on_time 覆盖率、字段缺失率、队列状态。"""
    span = int(days) if days is not None else publication.window_days()
    moment = _parse_ts(now) if now else datetime.now().astimezone()
    if moment is None:
        moment = datetime.now().astimezone()
    now_iso = _iso(moment)
    target = _target(category, db_path)
    con = connect(target)
    try:
        publication.ensure_schema(con)
        ensure_schema(con)
        articles_total = int(
            con.execute(
                "SELECT COUNT(*) FROM articles WHERE published_at IS NOT NULL AND published_at<>''"
            ).fetchone()[0]
        )
        rows = con.execute(
            "SELECT status, due_at, window_end, captured_at, reads, likes, favorites,"
            " comments, impressions FROM performance_windows"
            " WHERE subject_type='article' AND window_days=?",
            (span,),
        ).fetchall()
        queue_rows = con.execute(
            "SELECT state, COUNT(*) AS n FROM metrics_due_queue WHERE category=? GROUP BY state",
            (category,),
        ).fetchall()
    finally:
        con.close()

    status_counts = {status: 0 for status in publication.ALL_STATUSES}
    readings = 0
    missing = {field: 0 for field in publication.METRIC_FIELDS}
    recoverable = 0
    for row in rows:
        status_counts[str(row["status"])] = status_counts.get(str(row["status"]), 0) + 1
        due = _parse_ts(row["due_at"])
        end = _parse_ts(row["window_end"])
        if row["captured_at"]:
            readings += 1
            for field in publication.METRIC_FIELDS:
                if row[field] is None:
                    missing[field] += 1
        elif (due is not None and end is not None and due <= moment <= end):
            # 窗口内、还没有任何到期读数 —— 现在采就能落成 on_time。
            # 不按 status 过滤:R41 的 classify 对「只有到期前观测、窗口还没结束」的窗口
            # 也返回 overdue(状态名沿用旧口径),它们同样是本队列要补救的对象。
            recoverable += 1
    queue_states = {state: 0 for state in ALL_STATES}
    for row in queue_rows:
        queue_states[str(row["state"])] = int(row["n"])
    windows_total = sum(status_counts.values())
    return {
        "category": category,
        "window_days": span,
        "now": now_iso,
        "articles_with_published_at": articles_total,
        "windows": windows_total,
        "readings": readings,
        "status_counts": status_counts,
        "late_ratio": round(status_counts[publication.STATUS_LATE] / readings, 6) if readings else None,
        "on_time_coverage": round(status_counts[publication.STATUS_ON_TIME] / articles_total, 6)
        if articles_total else None,
        "on_time_share_of_readings": round(status_counts[publication.STATUS_ON_TIME] / readings, 6)
        if readings else None,
        "missing_counts": missing,
        "missing_ratio": {
            field: (round(missing[field] / readings, 6) if readings else None)
            for field in publication.METRIC_FIELDS
        },
        "recoverable_on_time_in_window": recoverable,
        "queue": queue_states,
        "queue_total": sum(queue_states.values()),
        "retry_policy": {
            "max_attempts": MAX_ATTEMPTS,
            "backoff_hours": list(RETRY_BACKOFF_HOURS),
            "success_stops": "窗口内成功一次即 done(取最早,重复采无意义)",
        },
    }
