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

import hashlib
import json
import os
import re
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
# 实测(mp_provider feed 取证 2026-11-21):后台 JSON 用 camelCase(readCount/diggCount/
# commentCount/showCount/displayURL/gidStr),别名必须同时覆盖 snake_case 与 camelCase。
_TITLE_KEYS = ("title", "title_text", "titleText")
_URL_KEYS = ("url", "article_url", "detail_url", "share_url", "public_url",
             "displayURL", "display_url", "shareURL")
_ID_KEYS = ("item_id", "group_id", "article_id", "gidStr", "gid_str")
_READ_KEYS = ("read_count", "read_cnt", "read_num", "view_count", "readCount", "readCnt")
_FAV_KEYS = ("favorite_count", "fav_cnt", "favorites_count", "collect_count",
             "favoriteCount", "favCount", "collectCount", "favsCount")
_LIKE_KEYS = ("like_count", "digg_count", "like_cnt", "diggCount", "likeCount")
_COMMENT_KEYS = ("comment_count", "comment_cnt", "comments_count", "commentCount")
_IMPRESSION_KEYS = ("impressions", "show_count", "showCount", "display_count")


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

    def _flatten(node: dict) -> dict:
        """把一层子对象(itemCounter/articleBase/shareInfo 等)并入本层再识别。

        实测后台列表:title 在 articleBase、计数在 itemCounter,
        不扁平化就永远配不上对。只并一层,不跨行乱拼。
        """
        merged: dict[str, Any] = {}
        for key, value in node.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if not isinstance(sub_value, (dict, list)):
                        merged.setdefault(sub_key, sub_value)
            elif not isinstance(value, (dict, list)):
                merged.setdefault(key, value)
        return merged

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        flat = _flatten(node)
        title = _first_key(flat, _TITLE_KEYS) or _first_key(node, _TITLE_KEYS)
        if isinstance(title, str) and title.strip():
            counts = {
                "reads": _as_int(_first_key(flat, _READ_KEYS)),
                "favorites": _as_int(_first_key(flat, _FAV_KEYS)),
                "likes": _as_int(_first_key(flat, _LIKE_KEYS)),
                "comments": _as_int(_first_key(flat, _COMMENT_KEYS)),
                "impressions": _as_int(_first_key(flat, _IMPRESSION_KEYS)),
            }
            if any(value is not None for value in counts.values()):
                item_id = _first_key(flat, _ID_KEYS) or _first_key(node, _ID_KEYS)
                url = _first_key(flat, _URL_KEYS) or _first_key(node, _URL_KEYS)
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


def parse_stat_modal(text: str) -> dict | None:
    """解析单篇"查看数据"弹层文本(R60 实测:互动区含**收藏**,公开页永远拿不到)。

    实测样例行:`展现量73`、`阅读量0`、`评论0`、`点赞0`、`收藏1`。
    保守口径:只认这些明确标签;认不出返回 None,绝不猜、不把缺失当 0。
    """
    if not text:
        return None
    title = ""
    for line in text.splitlines():
        line = line.strip()
        if line and line not in {"微头条", "部分数据更新有延迟"}:
            title = line
            break
    if not title:
        return None

    def grab(pattern: str, scope: str) -> int | None:
        m = re.search(pattern, scope)
        return _as_int(m.group(1)) if m else None

    idx = text.find("互动")
    has_actions = "查看" in text  # 现代列表 UI:行内有"查看评论/查看数据"操作链接
    if idx >= 0:
        # 互动四项只认"互动"分区标题之后的窗口:"查看评论"链接+紧邻数字会让
        # 全文匹配把无关数字当成评论数(实测出现过 评论1 ← 其实是操作链接)。
        scope = text[idx:idx + 160]
        flow_scope = text[:idx]
    elif has_actions:
        # 没有"互动"分区却有操作链接 ⇒ 互动数字不可信,宁缺毋滥;流量区仍可读。
        scope = ""
        flow_scope = text
    else:
        scope = flow_scope = text
    item = {
        "title": title,
        "item_id": None,
        "url": None,
        "reads": grab(r"阅读量\s*([0-9][0-9,]*)", flow_scope),
        "favorites": grab(r"收藏\s*([0-9][0-9,]*)", scope),
        "likes": grab(r"点赞\s*([0-9][0-9,]*)", scope),
        "comments": grab(r"评论\s*([0-9][0-9,]*)", scope),
        "impressions": grab(r"展现量\s*([0-9][0-9,]*)", flow_scope),
    }
    if not any(item[k] is not None for k in
               ("reads", "favorites", "likes", "comments", "impressions")):
        return None
    return item


def merge_modal_items(feed_items: list[dict], modal_items: list[dict]) -> list[dict]:
    """把"查看数据"弹层解析出的指标(含收藏)并回 feed 条目(同标题才并)。

    feed 有 url/item_id 但没有收藏;弹层有收藏但没有 url。按规范化标题配对:
    - 配对成功:来值非 None 才回填,已有非 None 值不覆盖(feed 与弹层本就同源);
    - 配不上:弹层条目原样保留(后面走唯一标题匹配,匹配不上只落证据,不乱挂)。
    """
    by_title: dict[str, dict] = {}
    for item in feed_items:
        key = str(item.get("title") or "").strip()
        if key:
            by_title.setdefault(key, item)
    out = list(feed_items)
    for modal in modal_items:
        key = str(modal.get("title") or "").strip()
        target = by_title.get(key)
        if target is None:
            out.append(modal)
            continue
        for field in ("reads", "favorites", "likes", "comments", "impressions"):
            if target.get(field) is None and modal.get(field) is not None:
                target[field] = modal[field]
    return out


def dedup_captured_items(items: list[dict]) -> list[dict]:
    """跨响应去重:同一作品在多条 XHR 响应里重复出现(实测 4 篇 ≈ 24 条)。

    key = 去 scheme/查询参数后的 host+path(feed 与弹层、不同响应的 url 参数不同
    但同一作品);没有 url 退回标题;同 key 的数值字段取"先到非 None"并集
    (同源读数是同一份数据,不存在"后一次刷新覆盖前一次"的语义,合并只会更全)。
    """
    def key_of(url: str, title: str) -> str:
        if url:
            from urllib.parse import urlsplit
            parts = urlsplit(url)
            return f"{parts.netloc.lower()}{parts.path.rstrip('/')}"
        return f"title:{title}"

    out: list[dict] = []
    index: dict[str, dict] = {}
    for item in items:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url and not title:
            continue  # url 和标题都没有的条目无法判重也无法匹配,直接丢
        key = key_of(url, title)
        first = index.get(key)
        if first is None:
            index[key] = item
            out.append(item)
            continue
        for field in ("url", "item_id", "reads", "favorites", "likes", "comments",
                      "impressions"):
            if first.get(field) is None and item.get(field) is not None:
                first[field] = item[field]
    return out


def _read_open_stat_modal(page: Any) -> str:
    """读当前打开的"查看数据"弹层文本,轮询到指标出现(或 12s 超时)。"""
    deadline = time.monotonic() + 12.0
    last = ""
    while time.monotonic() < deadline:
        try:
            text = page.evaluate(
                """() => { const nodes = document.querySelectorAll(
                     '[class*=modal], [class*=dialog], [class*=drawer], [class*=popup]');
                         let best = '';
                         for (const n of nodes) {
                           const t = (n.innerText || '').trim();
                           if (t.length > best.length) best = t;
                         }
                         return best.slice(0, 4000); }""") or ""
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            last = text
            if "收藏" in text or ("互动" in text and "展现量" in text):
                return text
        page.wait_for_timeout(1200)
    return last


def _collect_stat_modals(page: Any, context: Any, manage_url: str, max_rows: int = 8) -> list[str]:
    """生产采集:管理页逐行打开"查看数据"弹层读文本(自有作品低频、有限)。

    点击可能触发行内导航 ⇒ 每行处理完重新回管理页再点下一行;只读浏览,
    不改账号任何状态。返回每行弹层原文(解析失败也留证据)。
    """
    texts: list[str] = []
    for idx in range(max_rows):
        try:
            page.goto(manage_url, wait_until="domcontentloaded", timeout=60_000)
            stats = page.locator("text=查看数据")
            for _ in range(6):
                if stats.count() > idx:
                    break
                page.wait_for_timeout(1500)
            if stats.count() <= idx:
                break  # 没有更多行:正常结束
            try:
                stats.nth(idx).click(timeout=5000)
            except Exception:  # noqa: BLE001 - 行内控件遮挡/导航残留
                texts.append("")
                continue
            modal = _read_open_stat_modal(page)
            texts.append(modal)
            for extra in list(context.pages):
                if extra is not page:
                    try:
                        extra.close()
                    except Exception:  # noqa: BLE001
                        pass
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001 - 单行失败不带走整轮
            texts.append("")
            LOG.warn("account.fetch.modal_row_failed", idx=idx, error=str(exc)[:120])
    return texts


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


def _article_by_title_any(con, category: str, title: str) -> int | None:
    """登记判重专用:同名 1 篇也算"库内已有"(登记会造出更多同名行,宁缺毋滥)。"""
    rows = con.execute(
        "SELECT id FROM articles WHERE title=? AND category=?", (title, category)
    ).fetchall()
    return int(rows[0]["id"]) if rows else None


#: 注册进库的自有文章统一用这个 source 标记(溯源清楚,不会被当成竞品语料混淆)。
OWN_SOURCE = "own_account"


def register_own_work(con, category: str, item: dict, now: str) -> dict | None:
    """把"我方已发布但库内没有"的作品登记进类目库(R60 计划:发布↔库内映射)。

    只登记有 URL 的已发布作品;正文不在后台 feed 里,正文列以标题占位并注明
    (自有文章的取数在自有通道完成,不依赖正文;绝不伪造正文)。
    幂等:同 url_normalized 已存在就不重复插。
    """
    url = str(item.get("url") or "").strip()
    title = str(item.get("title") or "").strip()
    if not url or not title:
        return None
    normalized = normalize_url(url)
    hit = con.execute(
        "SELECT id FROM articles WHERE url_normalized=? AND category=?", (normalized, category)
    ).fetchone()
    if hit:
        return {"article_id": int(hit["id"]), "created": False}
    body = f"[自有账号已发布作品,正文未随后台 feed 返回,不做伪造] {title}"
    cur = con.execute(
        "INSERT INTO articles(category, source, source_item_id, title, content, excerpt,"
        " author, published_at, url, url_normalized, fetched_at, content_hash,"
        " completeness, credibility, processor_version)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            category, OWN_SOURCE, str(item.get("item_id") or ""), title, body,
            "自有账号发布作品(账号读数通道登记)", None, now, url, normalized, now,
            "own:" + hashlib.sha1(normalized.encode("utf-8")).hexdigest(),
            "own_work", "self_reported", "r60-account",
        ),
    )
    return {"article_id": int(cur.lastrowid), "created": True}


def register_pass(items: list[dict], categories: list[str],
                  own_work_category: str | None = None) -> dict:
    """R60 跨类目登记:自有作品只登记一次、只进目标类目。

    先扫全部类目库做 url/item_id 判重(命中即认为"库内已有",不重复登记),
    全库都没有的才登记进 own_work_category(默认 social;账号后台的自有作品
    以微头条/短文为主,由配置显式指定,不按标题瞎猜类目)。
    返回 {category: [已登记 item 摘要]} 供 fetch 汇总与审计。
    """
    target = own_work_category or str(
        (cfg.load_config().get("account") or {}).get("own_work_category") or "social")
    if target not in categories:
        categories = [*categories, target]
    registered_by_cat: dict[str, list[dict]] = {}
    seen_keys: set[str] = set()
    for item in items:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue
        key = normalize_url(url)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if any(str(i.get("title") or "").strip() == title for i in
               (registered_by_cat.get(target) or [])):
            continue  # 同一轮里同标题只登记一条(后台 feed 一行 + 弹层一行是同一作品)
        exists_in = None
        for category in categories:
            con = connect(cfg.category_db_path(category))
            try:
                _ensure_metrics_tables(con)
                article_id = _article_by_url(con, category, url)
                if article_id is None and item.get("item_id"):
                    article_id = _article_by_url(
                        con, category, f"https://www.toutiao.com/article/{item['item_id']}/")
                if article_id is None:
                    # 同名命中也是"库内已有":后台作品与库内采集文章 url 形态可能
                    # 不同(微头条 vs 文章 url),标题匹配是同一判定口径。
                    # 同名多篇同样拦下:登记只会制造更多同名行,不如不登记。
                    article_id = _article_by_title_any(con, category, title)
                if article_id is not None:
                    exists_in = (category, article_id)
                    break
            finally:
                con.close()
        if exists_in:
            continue
        con = connect(cfg.category_db_path(target))
        try:
            _ensure_metrics_tables(con)
            reg = register_own_work(con, target, item, cfg.now_iso())
            con.commit()
        finally:
            con.close()
        if reg and reg["created"]:
            LOG.info("account.fetch.own_registered", category=target,
                     article_id=reg["article_id"], title=title[:40])
            registered_by_cat.setdefault(target, []).append(
                {"title": title, "url": url, "article_id": reg["article_id"]})
    return registered_by_cat


def _ensure_metrics_tables(con) -> None:
    """article_metrics/metric_snapshots 在建库脚本切片里常被跳过;写入前幂等补齐,
    与 store 现有表结构逐列一致(IF NOT EXISTS,不动已有数据)。"""
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS article_metrics (
          article_id INTEGER PRIMARY KEY,
          reads INTEGER,
          likes INTEGER,
          favorites INTEGER,
          comments INTEGER,
          impressions INTEGER,
          account_baseline TEXT,
          data_source TEXT NOT NULL,
          credibility TEXT NOT NULL DEFAULT 'unverified',
          captured_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metric_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          article_id INTEGER NOT NULL,
          captured_at TEXT NOT NULL,
          reads INTEGER,
          likes INTEGER,
          favorites INTEGER,
          comments INTEGER,
          impressions INTEGER,
          data_source TEXT
        );
        """
    )
    con.commit()


def match_and_write(category: str, items: list[dict], *, db_path: Path | None = None,
                    apply: bool = True, allow_register: bool = False) -> dict:
    """把账号取回的条目按高置信匹配写进该类目库;返回 matched/unmatched/写入回执。

    登记自有作品默认关闭(allow_register=False):同一作品会被登记进每个被遍历的
    类目库,造成跨类目污染。登记统一走 register_pass(全库判重、只进目标类目)。
    """
    from ttxt.analysis import metrics_import

    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    matched_rows: list[dict] = []
    unmatched: list[dict] = []
    registered = 0
    con = connect(target)
    _ensure_metrics_tables(con)
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
            if article_id is None and allow_register and apply and item.get("url") and item.get("title"):
                # R60 映射:我方已发布作品不在库 → 先登记(source=own_account)再挂指标。
                # 只在 --apply 时登记:dry-run 不动库;非自有数据到不了这里(入口仅自有后台)。
                reg = register_own_work(con, category, item, cfg.now_iso())
                if reg is not None:
                    if reg["created"]:
                        registered += 1
                        LOG.info("account.fetch.own_registered", category=category,
                                 article_id=reg["article_id"])
                    article_id = _article_by_url(con, category, item["url"])
                    how = "own_registered"
            if article_id is None:
                unmatched.append(item)
                continue
            row = {"article_id": article_id}
            for field in ("reads", "favorites", "likes", "comments", "impressions"):
                if item.get(field) is not None:
                    row[field] = item[field]
            row["_matched_by"] = how
            matched_rows.append(row)
        con.commit()  # 登记自有作品必须落库:回滚会让下轮重复登记并丢映射
    finally:
        con.close()

    if not matched_rows:
        return {"category": category, "items": len(items), "matched": 0,
                "unmatched": len(unmatched), "registered": registered, "applied": False,
                "write": None, "unmatched_items": unmatched}
    if not apply:
        return {"category": category, "items": len(items), "matched": len(matched_rows),
                "unmatched": len(unmatched), "registered": registered, "applied": False,
                "write": None, "unmatched_items": unmatched}
    write = metrics_import.write_metric_rows(
        category, matched_rows, SOURCE_ID, db_path=target)
    return {"category": category, "items": len(items), "matched": len(matched_rows),
            "unmatched": len(unmatched), "registered": registered, "applied": True,
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

def _pin_playwright_tmp() -> None:
    """Playwright driver 用 os.tmpdir() 建 artifacts;系统 %TEMP% 实测 EPERM,
    和采集侧 fetcher 一样把 TMP/TEMP/TMPDIR 钉到项目内(顺带复用其陈旧清理)。"""
    from ttxt.crawler import fetcher

    pw_tmp = str(fetcher._pw_artifacts_dir())
    for var in ("TMP", "TEMP", "TMPDIR"):
        os.environ[var] = pw_tmp


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
        _pin_playwright_tmp()
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


def account_fetch_metrics(apply: bool = True, categories: list[str] | None = None,
                          force: bool = False) -> dict:
    """用已保存的账号 profile 低频取回自有文章指标(含 favorites)。

    ``force`` 只允许人工排障时越过 2h 节流,且必须留 warn 审计;调度器永远不传。
    """
    from patchright.sync_api import sync_playwright

    from ttxt.crawler import fetcher

    state = load_state()
    left = interval_blocked(state)
    if left is not None and force:
        LOG.warn("account.fetch.force_override", left_s=left, note="人工排障越过节流,留审计")
    elif left is not None:
        LOG.warn("account.fetch.throttled", left_s=left)
        return {"status": "throttled", "note": f"距上次成功取数不足 {MIN_FETCH_INTERVAL_S}s,还需 {left:g}s"}

    payload_count = 0
    captured_items: list[dict] = []
    pw = None
    handle = None
    try:
        ACCOUNT_PROFILE.mkdir(parents=True, exist_ok=True)
        handle = browser_lock.acquire(ACCOUNT_PROFILE, wait_s=30, tag="account_metrics")
        _pin_playwright_tmp()
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

        seen_requests: list[str] = []
        raw_bodies: list[dict] = []
        row_texts: dict[str, list[str]] = {}
        page_texts: dict[str, str] = {}
        clicks: dict[str, dict] = {}
        diag_path: str | None = None

        def on_response(response: Any) -> None:
            nonlocal payload_count
            try:
                url = str(response.url)
                host = (url.split("/")[2] if "://" in url else "")
                if not host.endswith("toutiao.com"):
                    return
                ctype = str(response.headers.get("content-type") or "")
                if "json" not in ctype:
                    return
                try:
                    body = response.text()
                except Exception:  # noqa: BLE001 - 响应体已被释放
                    return
                data = json.loads(body)
            except Exception:  # noqa: BLE001 - 非 JSON/已关闭响应直接跳过
                return
            items = extract_metric_items(data)
            if items:
                payload_count += 1
                captured_items.extend(items)
            elif len(raw_bodies) < 20:
                # 取证:认不出字段的 JSON 原样留一份,离线核对真实字段名后再扩别名
                raw_bodies.append({"url": url[:200], "body": body[:4000]})

        def on_request(request: Any) -> None:
            if "toutiao.com" in request.url and len(seen_requests) < 80:
                seen_requests.append(f"{request.resource_type}:{request.url[:160]}")

        page.on("response", on_response)
        page.on("request", on_request)
        page_final: dict[str, str] = {}
        for url in DATA_PAGES:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(8000)
                for _ in range(3):  # 触发懒加载/分页:统计页列表接口要滚动/久等才发
                    try:
                        page.mouse.wheel(0, 2400)
                    except Exception:  # noqa: BLE001
                        pass
                    page.wait_for_timeout(3000)
                page.wait_for_timeout(6000)
                page_final[url] = f"{page.url} | {(page.title() or '')[:60]}"
                # 取证:列表已渲染的行文本(UI 上"收藏"列可见,渲染文本是 DOM 兜底的数据源)
                try:
                    row_texts[url] = page.evaluate(
                        """() => Array.from(document.querySelectorAll('a[href*=".toutiao.com/"]'))
                             .map(a => { const row = a.closest('li,tr') || a.closest('div');
                                return row ? (row.innerText || '').slice(0, 400) : ''; })
                             .filter(t => t.trim()).slice(0, 20)""")
                except Exception:  # noqa: BLE001
                    row_texts[url] = []
                # 取证 A:整页可见文本。列表表头(展现/阅读/评论/点赞/收藏)与数值列
                # 只有渲染出来才看得见;行选择器没抓到时这里是兜底证据。
                try:
                    page_texts[url] = (page.evaluate("() => document.body.innerText || ''") or "")[:6000]
                except Exception:  # noqa: BLE001
                    page_texts[url] = ""
                # 取证 B 由下面的生产采集覆盖(逐行"查看数据"弹层),这里不再单独点击。
            except Exception as exc:  # noqa: BLE001 - 单页失败不带走整轮
                page_final[url] = f"FAILED {str(exc)[:120]}"
                LOG.warn("account.fetch.page_failed", url=url, error=str(exc)[:160])

        # 生产采集:管理页逐行打开"查看数据"弹层 → 解析含**收藏**的单篇指标。
        # feed(无收藏)+ 弹层(有收藏)按标题合并后才进入匹配写库。
        manage_url = DATA_PAGES[0]
        modal_texts = _collect_stat_modals(page, context, manage_url)
        clicks[manage_url] = {"modals": modal_texts}  # 证据:原文全留
        modal_items = []
        for text in modal_texts:
            parsed = parse_stat_modal(text) if text else None
            if parsed is not None:
                modal_items.append(parsed)
        if modal_items:
            before = len(captured_items)
            captured_items = merge_modal_items(captured_items, modal_items)
            LOG.info("account.fetch.modal_metrics", modals=len(modal_texts),
                     parsed=len(modal_items), merged_items=len(captured_items) - before)
            page_texts[manage_url] = page_texts.get(manage_url, "") or (
                modal_texts[0][:2000] if modal_texts else "")

        if raw_bodies or not captured_items or any(row_texts.values()) or page_texts or clicks:
            out_dir = cfg.INTELLIGENCE_DIR / "account_metrics"
            out_dir.mkdir(parents=True, exist_ok=True)
            diag = out_dir / f"diag-fetch-{cfg.now_iso().replace(':', '').replace('-', '')}.json"
            diag.write_text(json.dumps(
                {"pages": page_final, "requests": seen_requests, "unparsed_json": raw_bodies,
                 "row_texts": row_texts, "page_texts": page_texts, "clicks": clicks},
                ensure_ascii=False, indent=2), encoding="utf-8")
            diag_path = str(diag)
            LOG.info("account.fetch.diag", path=str(diag), requests=len(seen_requests))

        LOG.info("account.fetch.captured", responses=payload_count, items=len(captured_items))
        if not captured_items:
            return {"status": "no_data", "note": "后台响应里没有可识别的文章指标(页面结构变化?)",
                    "pages": page_final, "diag": diag_path}

        if categories is None:
            categories = [str(slug) for slug in cfg.load_config().get("categories", []) if slug]
        captured_items = dedup_captured_items(captured_items)
        LOG.info("account.fetch.deduped", items=len(captured_items))
        registered_by_cat: dict[str, list[dict]] = {}
        if apply:
            # R60:登记与匹配解耦 —— 先全库判重、只登记一次、只进目标类目(默认 social),
            # 再从匹配写库开始,目标类目就能按 url 命中刚登记的文章。
            registered_by_cat = register_pass(captured_items, categories)
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
                "registered": registered_by_cat, "categories": summaries}
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
