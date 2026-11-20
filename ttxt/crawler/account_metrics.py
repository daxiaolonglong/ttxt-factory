"""R60 G1:头条**自有账号**读数回流 —— 补齐公开页拿不到的"收藏",并回流自己文章的真实阅读/点赞。

为什么必须走账号通道(已核实的事实):
- ``metrics_pages.py`` 从公开文章页 ``__INITIAL_STATE__.itemCounter`` 只取到
  read/digg/comment/show;**favorites 公开页没有该字段**(规则 41 两次证伪,恒 NULL);
- 我方发布文章的真实表现(含收藏)只有头条创作中心(mp.toutiao.com)有;
- 用户明确授权"该授权就授权":扫码登录**自己的账号**一次,之后自动低频只读自己的后台。

边界(一条都不放宽):
- **只读自有账号数据**:入口只有 mp.toutiao.com 创作中心,数据天然限定为自有文章;
  匹配不上的条目只落证据 JSON,绝不乱挂到库里任何文章;
- **独立 profile**(``02_crawler/.account/profile``,已 gitignore):与公开采集的
  ``02_crawler/.browser/profile`` 完全分离,账号会话不会被采集轮次重置,反之亦然;
- **独立跨进程锁**:复用 browser_lock,但锁文件在 .account/profile.lock,与采集互不冒充;
- **低频硬下限**:两次成功取数间隔 ≥ 2 小时(scheduler job interval_min 同样 120);
- SSRF 双层防护照抄采集侧(``--host-resolver-rules`` + 逐请求 route 拦截);
- 写入唯一入口是 ``metrics_import.write_metric_rows``(source 不在
  ``config.metrics.authorized_sources`` 白名单直接被 PermissionError 拒绝,可信度低不得覆盖高);
- 不落任何 cookie/token 到 git 可见路径;日志只记事件与计数。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ttxt import config as cfg
from ttxt.crawler import browser_lock
from ttxt.crawler.clean import normalize_url
from ttxt.db import connect
from ttxt.logging import get_logger

LOG = get_logger("account.metrics")

ACCOUNT_DIR = cfg.CRAWLER_DIR / ".account"
ACCOUNT_PROFILE = ACCOUNT_DIR / "profile"
STATE_FILENAME = "account_metrics_state.json"

SOURCE_ID = "toutiao_account"

#: 两次成功取数的硬下限(秒)。调度间隔也是 120 分钟,这里是第二道闸,配置挡不住代码。
MIN_FETCH_INTERVAL_S = 2 * 3600

LOGIN_URL = "https://mp.toutiao.com/"
#: 只读自有数据的两个后台页面;JSON 监听从这些页面的响应里取数,不做 DOM 猜测。
DATA_PAGES = [
    "https://mp.toutiao.com/profile_v4/manage/content/all",
    "https://mp.toutiao.com/profile_v4/statistics/content",
]
ALLOWED_HOST_SUFFIX = "mp.toutiao.com"

#: 登录成功的判据:toutiao 域下出现任一登录态会话 cookie(扫码后由服务端下发)。
SESSION_COOKIE_NAMES = {"sessionid", "sid_tt", "sid_guard"}

# 后台接口的字段名在不同页面不一致,这里做保守归一:只认已知键,认不出就整条跳过,
# 不猜、不把缺失当 0(规则 6)。
_TITLE_KEYS = ("title", "title_text", "titleText")
_URL_KEYS = ("url", "article_url", "detail_url", "share_url", "public_url")
_ID_KEYS = ("item_id", "group_id", "article_id")
_READ_KEYS = ("read_count", "read_cnt", "read_num", "view_count")
_FAV_KEYS = ("favorite_count", "fav_cnt", "favorites_count", "collect_count")
_LIKE_KEYS = ("like_count", "digg_count", "like_cnt")
_COMMENT_KEYS = ("comment_count", "comment_cnt", "comments_count")


def state_path() -> Path:
    return cfg.CONTROL_DIR / STATE_FILENAME


def load_state() -> dict:
    path = state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def interval_blocked(state: dict, now: datetime | None = None) -> float | None:
    """距上次**成功**取数不足硬下限时,返回还需等待的秒数;否则 None。"""
    last = _parse_iso(state.get("last_fetch_ok_at"))
    if last is None:
        return None
    now = now or datetime.now(timezone.utc)
    left = MIN_FETCH_INTERVAL_S - (now - last).total_seconds()
    return round(left, 1) if left > 0 else None


# ---------------------------------------------------------------------------
# 纯函数层:响应解析与匹配(不依赖浏览器,测试全覆盖这层)
# ---------------------------------------------------------------------------

def _first_key(d: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        number = int(float(text))
    except ValueError:
        return None
    return number if number >= 0 else None


def extract_metric_items(payload: Any) -> list[dict]:
    """从后台 JSON 响应里递归找"文章指标条目":同时有标题和至少一个计数字段的 dict。

    保守口径:一个字段都认不出就不产出;认得出的字段才写入,认不出保持缺失(不填 0)。
    """
    found: list[dict] = []
    seen_signatures: set[tuple] = set()

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        title = _first_key(node, _TITLE_KEYS)
        if isinstance(title, str) and title.strip():
            counts = {
                "reads": _as_int(_first_key(node, _READ_KEYS)),
                "favorites": _as_int(_first_key(node, _FAV_KEYS)),
                "likes": _as_int(_first_key(node, _LIKE_KEYS)),
                "comments": _as_int(_first_key(node, _COMMENT_KEYS)),
            }
            if any(value is not None for value in counts.values()):
                item_id = _first_key(node, _ID_KEYS)
                url = _first_key(node, _URL_KEYS)
                signature = (title.strip(), str(item_id or ""), str(url or ""))
                if signature not in seen_signatures:
                    seen_signatures.add(signature)
                    found.append({
                        "title": title.strip(),
                        "item_id": str(item_id) if item_id not in (None, "") else None,
                        "url": str(url) if url not in (None, "") else None,
                        **counts,
                    })
        for value in node.values():
            walk(value)

    walk(payload)
    return found


def _article_by_url(con, category: str, url: str) -> int | None:
    hit = con.execute(
        "SELECT id FROM articles WHERE url_normalized=? AND category=?",
        (normalize_url(url), category),
    ).fetchone()
    return int(hit["id"]) if hit else None


def _article_by_title(con, category: str, title: str) -> int | None:
    rows = con.execute(
        "SELECT id FROM articles WHERE title=? AND category=?", (title, category)
    ).fetchall()
    # 高置信匹配:同名多篇 ⇒ 无法确定是哪一篇 ⇒ 宁缺毋滥。
    return int(rows[0]["id"]) if len(rows) == 1 else None


def match_and_write(category: str, items: list[dict], *, db_path: Path | None = None,
                    apply: bool = True) -> dict:
    """把账号取回的条目按高置信匹配写进该类目库;返回 matched/unmatched/写入回执。"""
    from ttxt.analysis import metrics_import

    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    matched_rows: list[dict] = []
    unmatched: list[dict] = []
    con = connect(target)
    try:
        for item in items:
            article_id = None
            how = None
            if item.get("url"):
                article_id = _article_by_url(con, category, item["url"])
                how = "url"
            if article_id is None and item.get("item_id"):
                article_id = _article_by_url(
                    con, category, f"https://www.toutiao.com/article/{item['item_id']}/")
                if article_id is not None:
                    how = "item_id"
            if article_id is None and item.get("title"):
                article_id = _article_by_title(con, category, item["title"])
                if article_id is not None:
                    how = "title_unique"
            if article_id is None:
                unmatched.append(item)
                continue
            row = {"article_id": article_id}
            for field in ("reads", "favorites", "likes", "comments"):
                if item.get(field) is not None:
                    row[field] = item[field]
            row["_matched_by"] = how
            matched_rows.append(row)
    finally:
        con.close()

    if not matched_rows:
        return {"category": category, "items": len(items), "matched": 0,
                "unmatched": len(unmatched), "applied": False,
                "write": None, "unmatched_items": unmatched}
    if not apply:
        return {"category": category, "items": len(items), "matched": len(matched_rows),
                "unmatched": len(unmatched), "applied": False,
                "write": None, "unmatched_items": unmatched}
    write = metrics_import.write_metric_rows(
        category, matched_rows, SOURCE_ID, db_path=target)
    return {"category": category, "items": len(items), "matched": len(matched_rows),
            "unmatched": len(unmatched), "applied": True,
            "write": {k: write.get(k) for k in
                      ("written", "updated", "snapshots", "filled_from_null",
                       "protected_by_credibility", "row_errors")},
            "unmatched_items": unmatched}


def _write_evidence(summary: dict) -> str | None:
    if not summary.get("unmatched_items") and not summary.get("matched"):
        return None
    out_dir = cfg.INTELLIGENCE_DIR / "account_metrics"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = cfg.now_iso().replace(":", "").replace("-", "")
    path = out_dir / f"{summary.get('category', 'x')}-account-{stamp}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 浏览器层:登录(一次)与取数(低频)
# ---------------------------------------------------------------------------

def _account_browser_kwargs(headless: bool) -> dict[str, Any]:
    from ttxt.crawler import fetcher

    conf = cfg.load_config().get("browser", {}) or {}
    kwargs: dict[str, Any] = {"headless": bool(headless)}
    executable = conf.get("executable_path")
    if executable and Path(executable).exists():
        kwargs["executable_path"] = str(executable)
    flags = ["--disk-cache-size=67108864", f"--host-resolver-rules={fetcher.ssrf_host_resolver_rules()}"]
    kwargs["args"] = flags
    return kwargs


def _cookies_logged_in(context: Any) -> bool:
    try:
        cookies = context.cookies()
    except Exception:  # noqa: BLE001 - 浏览器已关等场景按未登录处理
        return False
    for cookie in cookies:
        domain = str(cookie.get("domain") or "")
        if domain.endswith("toutiao.com") and cookie.get("name") in SESSION_COOKIE_NAMES:
            return True
    return False


def account_login(timeout_s: int = 180) -> dict:
    """打开有头浏览器让用户扫码登录自己的账号;登录成功后 profile 长期复用。"""
    from patchright.sync_api import sync_playwright

    from ttxt.crawler import fetcher

    ACCOUNT_PROFILE.mkdir(parents=True, exist_ok=True)
    handle = browser_lock.acquire(ACCOUNT_PROFILE, wait_s=10, tag="account_login")
    pw = None
    try:
        pw = sync_playwright().start()
        context = pw.chromium.launch_persistent_context(
            str(ACCOUNT_PROFILE), **_account_browser_kwargs(headless=False))
        context.on("page", lambda page: fetcher._install_request_guard(page))
        page = context.pages[0] if context.pages else context.new_page()
        fetcher._install_request_guard(page)
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        LOG.info("account.login.opened", url=LOGIN_URL, timeout_s=int(timeout_s))
        deadline = time.monotonic() + max(30, int(timeout_s))
        while time.monotonic() < deadline:
            if _cookies_logged_in(context):
                state = load_state()
                state["last_login_at"] = cfg.now_iso()
                save_state(state)
                LOG.info("account.login.ok")
                return {"status": "ok", "note": "登录成功,profile 已保存,后续取数无需再扫码"}
            time.sleep(2)
        LOG.warn("account.login.timeout")
        return {"status": "timeout", "note": f"{timeout_s}s 内未检测到登录态 cookie,未扫码或扫码未完成"}
    except Exception as exc:  # noqa: BLE001 - 浏览器失败必须如实上报
        LOG.error("account.login.failed", error=str(exc)[:200])
        return {"status": "error", "note": f"登录流程失败:{str(exc)[:200]}"}
    finally:
        try:
            if pw is not None:
                pw.stop()
        except Exception:  # noqa: BLE001
            pass
        handle.release()


def account_fetch_metrics(apply: bool = True, categories: list[str] | None = None) -> dict:
    """用已保存的账号 profile 低频取回自有文章指标(含 favorites)。"""
    from patchright.sync_api import sync_playwright

    from ttxt.crawler import fetcher

    state = load_state()
    left = interval_blocked(state)
    if left is not None:
        LOG.warn("account.fetch.throttled", left_s=left)
        return {"status": "throttled", "note": f"距上次成功取数不足 {MIN_FETCH_INTERVAL_S}s,还需 {left:g}s"}

    payload_count = 0
    captured_items: list[dict] = []
    pw = None
    handle = None
    try:
        ACCOUNT_PROFILE.mkdir(parents=True, exist_ok=True)
        handle = browser_lock.acquire(ACCOUNT_PROFILE, wait_s=30, tag="account_metrics")
        pw = sync_playwright().start()
        context = pw.chromium.launch_persistent_context(
            str(ACCOUNT_PROFILE), **_account_browser_kwargs(headless=True))
        page = context.pages[0] if context.pages else context.new_page()
        fetcher._install_request_guard(page)
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        if not _cookies_logged_in(context):
            LOG.warn("account.fetch.not_logged_in")
            return {"status": "not_logged_in",
                    "note": "profile 无登录态(未登录或会话过期),先跑 account-login 扫码"}

        def on_response(response: Any) -> None:
            nonlocal payload_count
            try:
                host = (response.url.split("/")[2] if "://" in response.url else "")
                if not host.endswith(ALLOWED_HOST_SUFFIX):
                    return
                ctype = str(response.headers.get("content-type") or "")
                if "json" not in ctype:
                    return
                data = response.json()
            except Exception:  # noqa: BLE001 - 非 JSON/已关闭响应直接跳过
                return
            items = extract_metric_items(data)
            if items:
                payload_count += 1
                captured_items.extend(items)

        page.on("response", on_response)
        for url in DATA_PAGES:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(4000)
            except Exception as exc:  # noqa: BLE001 - 单页失败不带走整轮
                LOG.warn("account.fetch.page_failed", url=url, error=str(exc)[:160])

        LOG.info("account.fetch.captured", responses=payload_count, items=len(captured_items))
        if not captured_items:
            return {"status": "no_data", "note": "后台响应里没有可识别的文章指标(页面结构变化?)",
                    "pages": DATA_PAGES}

        if categories is None:
            categories = [str(slug) for slug in cfg.load_config().get("categories", []) if slug]
        summaries = []
        total_matched = 0
        for category in categories:
            summary = match_and_write(category, captured_items, apply=apply)
            summaries.append({**{k: v for k, v in summary.items() if k != "unmatched_items"},
                              "evidence": _write_evidence(summary)})
            total_matched += summary["matched"]
        if apply and total_matched > 0:
            state["last_fetch_ok_at"] = cfg.now_iso()
            state["last_matched"] = total_matched
            save_state(state)
        return {"status": "ok" if total_matched else "no_match",
                "items": len(captured_items), "responses": payload_count,
                "categories": summaries}
    except browser_lock.ProfileBusyError as exc:
        LOG.warn("account.fetch.busy", error=str(exc)[:160])
        return {"status": "busy", "note": f"账号 profile 正被占用:{str(exc)[:160]}"}
    except Exception as exc:  # noqa: BLE001 - 如实上报,不伪造成 ok
        LOG.error("account.fetch.failed", error=str(exc)[:200])
        return {"status": "error", "note": f"取数失败:{str(exc)[:200]}"}
    finally:
        try:
            if pw is not None:
                pw.stop()
        except Exception:  # noqa: BLE001
            pass
        if handle is not None:
            handle.release()
