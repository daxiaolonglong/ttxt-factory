"""P21 + P22:头条公开文章页互动指标采集(本文章自己的阅读/点赞/评论/展现量)。

取证依据(07_tmp/r24_metrics_probe/、07_tmp/r25_render_counters/):
- probe1.json:头条 robots.txt 不禁 ``/article/``;但纯 HTTP 抓到的是字节跳动 JSVMP 反爬壳
  (三条不同文章 URL 的响应字节数完全一样,正文与文章 id 都不出现)⇒ 只能走项目正常浏览器渲染通道;
- probe2_render.json / probe3_selectors.json:渲染页 DOM 上**本文章**只有
  ``.detail-side-interaction .detail-like[aria-label="点赞N"]`` 与
  ``.detail-interaction-comment[aria-label="…N评论"]``(兜底 ``#comment-area .title`` 的 "评论 N");
  "16万阅读" 这类数字出现在"推荐阅读"卡片里(**那是别的文章的**阅读数,还按万取整);
- **probe5_offline_keys.json / probe6_render_data.json(对同一批已存页面的离线复核,更正上一轮的结论)**:
  页面里 ``<script id="RENDER_DATA">`` 是 percent-encoded JSON,其中
  ``$.data.itemId`` 等于本页 URL 的文章 id(归属可证明),
  ``$.data.itemCell.itemCounter = {commentCount 47, diggCount 437, readCount 8937,
  showCount 111790, shareCount 1801, repinCount 1927, forwardCount 15, buryCount 0, danmuCount 0}``,
  且 diggCount / commentCount 与工具条 aria-label 的 437 / 47 **完全一致**(交叉验证通过)
  ⇒ 阅读数与展现量公开页**有**精确值,不必再用卡片估算。

诚实边界(07_rules/CORE_RULES.md 规则 41):
- 主机白名单 + 逐 URL robots 闸门:不放行 ⇒ 一次抓取都不许发生;
- 内嵌计数只有 **payload 里的文章 id == 本页 URL 的文章 id** 时才许用;对不上、取不到 id、
  没有 URL 上下文 ⇒ 整块丢弃,退回 DOM 兜底;
- 展现量 < 阅读量(物理不可能)⇒ 拒绝 impressions,其余照写;
- 只取本文章计数;推荐卡片、别人评论的点赞数一律不算本文章的指标;
- shareCount / repinCount / forwardCount / buryCount / danmuCount 语义未证实
  ⇒ 只进报告当证据,**绝不入库、绝不改名冒充收藏**;favorites 公开页仍然没有 ⇒ 恒为 NULL;
- 取不到 ⇒ NULL,绝不折算 0;页面显示真 0 ⇒ 如实写 0;
- 默认 dry-run(只报告不落库),``apply=True`` 才写;写库走 metrics_import 的可信度合并 + 时序快照。
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ttxt import config as cfg
from ttxt.analysis import metrics_import
from ttxt.db import connect
from ttxt.logging import get_logger

from . import fetcher, robots

LOG = get_logger(__name__)

SOURCE_ID = "toutiao_public_article"
PUBLIC_HOSTS = frozenset({"www.toutiao.com", "m.toutiao.com"})

METRIC_FIELDS = ("reads", "likes", "favorites", "comments", "impressions")

# ``favorites`` 的语义在公开页始终没取到证(见模块开头规则 41),所以**永不入库、永不写 0**;
# 报告里如实标成"语义未取证",避免被误读成"这篇文章没人收藏"。
UNPROVEN_FIELDS = ("favorites",)

# P33 逐篇结果台账:每条候选必须落到唯一一个可解释状态。
OUTCOME_OK = "metrics_ok"                      # 拿到本文章自己的真实计数
OUTCOME_NO_COUNTS = "no_public_counts"         # 页面能打开,但本文章没有任何可归属计数
OUTCOME_GONE = "article_gone"                  # 404/410:内容不存在(平台删除/下架)
OUTCOME_LEGAL_BLOCKED = "blocked_by_legal"     # 451:因法律/政策要求不可用(P34 起单独一列)
OUTCOME_BLOCKED = "access_blocked"             # 401/403/429:访问受限(不放宽任何保护)
OUTCOME_FETCH_FAILED = "fetch_failed"          # 网络/浏览器等其他失败
OUTCOME_ROBOTS_REFUSED = "robots_refused"      # robots 不放行 ⇒ 一次请求都不发
# P34(真人验收裁定第 2 条)口径修正:451 是「因法律要求不可用」,可能是临时屏蔽,
# 它**不是**「内容不存在」的证据,也不能当作「以后不用再请求」的依据。
# ⇒ 状态码集合分开维护:只有 404/410 构成可追踪的「已下架」(见 LINK_STATUS_KEY),
#   451 单列 blocked_by_legal,既不写指标也不进跳过清单,下一轮照常复查。
_GONE_STATUSES = frozenset({404, 410})
_LEGAL_STATUSES = frozenset({451})
_BLOCKED_STATUSES = frozenset({401, 403, 429})


def _classify_fetch_failure(exc: BaseException) -> tuple[str, int | None]:
    """把抓取异常归类;状态码来自 fetcher 的 FetchError,没有状态码就是网络/渲染失败。"""
    raw = getattr(exc, "status", None)
    status = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else None
    if status in _GONE_STATUSES:
        return OUTCOME_GONE, status
    if status in _LEGAL_STATUSES:
        return OUTCOME_LEGAL_BLOCKED, status
    if status in _BLOCKED_STATUSES:
        return OUTCOME_BLOCKED, status
    return OUTCOME_FETCH_FAILED, status


def _record_outcome(report: dict, outcome: dict) -> None:
    """台账条目入报告并计数(计数与条目必须永远一致,否则验收对不上账)。"""
    report["outcomes"].append(outcome)
    key = str(outcome["outcome"])
    report["outcome_counts"][key] = report["outcome_counts"].get(key, 0) + 1


# ---------------------------------------------------------------------------
# P34 ③(真人验收裁定第 3 条):已确认下架文章的可追踪跳过台账
#
# 落点选择:**只用现成的 ``articles_meta`` kv 表**(key = ``metrics_link_status``),
# 不新建表、不给 ``articles`` 加字段 —— P33 红线是"不得增加新表/新字段除非先证明现有
# 结构无法完成",而这里要的只是 article_id -> 状态 的映射,kv 完全够用
# (与 ``viral_calibration``、``dispatch_state`` 同一套约定;红线由测试锁住)。
#
# 三条硬规矩(都有测试):
# 1. 只有 404/410(``article_gone``,内容确实不存在)才进台账;451/403/网络失败不进 ——
#    它们是临时故障或法律屏蔽,进了就等于把一次误判永久化;
# 2. 记录必须带取证时间并且**会过期**(GONE_RETRY_DAYS):平台可能恢复内容、URL 也可能是我们自己拼错;
#    过期后重新取证,结果好就把旧记录撤销;
# 3. dry-run 什么都不写;只有 ``apply=True`` 才落库(与"默认不改生产数据"口径一致)。
LINK_STATUS_KEY = "metrics_link_status"
GONE_RETRY_DAYS = 90


def _read_link_status(con) -> dict[str, dict]:
    row = con.execute("SELECT value FROM articles_meta WHERE key=?", (LINK_STATUS_KEY,)).fetchone()
    if not row:
        return {}
    try:
        payload = json.loads(row["value"])
    except (TypeError, ValueError):
        # 台账坏了不能连累采集:当作空台账重来,但必须留日志(不静默吞掉)
        LOG.warn("metrics.link_status.corrupt", key=LINK_STATUS_KEY)
        return {}
    if not isinstance(payload, dict):
        LOG.warn("metrics.link_status.corrupt", key=LINK_STATUS_KEY)
        return {}
    return {str(key): value for key, value in payload.items() if isinstance(value, dict)}


def load_link_status(category: str, db_path: Path | None = None) -> dict[str, dict]:
    """只读:本类目的链接状态台账(article_id -> {status, outcome, url, checked_at, reason})。"""
    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    con = connect(target)
    try:
        return _read_link_status(con)
    finally:
        con.close()


def _status_fresh(entry: dict, now: datetime) -> bool:
    """台账记录是否仍在有效期内。时间读不出来 ⇒ 当过期处理(宁可多查一次,不做永久拉黑)。"""
    if int(entry.get("status") or 0) not in _GONE_STATUSES:
        return False
    raw = str(entry.get("checked_at") or "")
    try:
        checked = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if checked.tzinfo is None:
        checked = checked.astimezone()  # naive 时间按本机时区解释,和 now 同一口径才能相减
    return (now - checked).days < GONE_RETRY_DAYS


def _update_link_status(category: str, db_path: Path | None, report: dict, apply: bool, stamp: str) -> None:
    """把本轮确认的「内容不存在」写进台账;重新取证成功的撤销旧记录。"""
    if not apply:
        return
    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    con = connect(target)
    try:
        current = _read_link_status(con)
        payload = dict(current)
        recorded = cleared = 0
        for outcome in report["outcomes"]:
            key = str(outcome["article_id"])
            if outcome["outcome"] == OUTCOME_GONE:
                payload[key] = {
                    "status": outcome["status"],
                    "outcome": OUTCOME_GONE,
                    "url": outcome["url"],
                    "checked_at": stamp,
                    "reason": str(outcome.get("reason") or "")[:200],
                }
                recorded += 1
            elif outcome["outcome"] == OUTCOME_OK and key in payload:
                payload.pop(key)  # 这次取到了 ⇒ 旧的"已下架"结论作废,不能既成功又留着 404 记录
                cleared += 1
        if payload != current:
            con.execute(
                "INSERT OR REPLACE INTO articles_meta(key, value) VALUES(?, ?)",
                (LINK_STATUS_KEY, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            )
            con.commit()
        report["link_status_recorded"] = recorded
        report["link_status_cleared"] = cleared
    finally:
        con.close()


_COUNT_RE = re.compile(r"^([+]?\d[\d,]*(?:\.\d+)?)([万wkW]?)$")
_TOOLBAR_SELECTOR = ".detail-side-interaction"
_READ_SELECTORS = (".article-meta", ".article-header", "header")

# P22:页面内嵌 RENDER_DATA 的计数字段映射(实测 probe6_render_data.json)。
# 只映射语义已证实的四个;shareCount/repinCount/forwardCount/buryCount/danmuCount 语义未证实,
# 只作为证据留在报告里,绝不入库,更不許改名成 favorites。
RENDER_SCRIPT_ID = "RENDER_DATA"
_COUNTER_FIELD = {
    "readCount": "reads",
    "diggCount": "likes",
    "commentCount": "comments",
    "showCount": "impressions",
}
_ID_KEYS = ("itemId", "item_id", "gid", "group_id", "articleId")
_RENDER_BLOCK_RE = re.compile(
    r'<script[^>]+id=["\']' + RENDER_SCRIPT_ID + r'["\'][^>]*>(.*?)</script>', re.S | re.I
)
_ARTICLE_ID_PATTERNS = (
    re.compile(r"/article/(\d{6,25})(?:[/?#]|$)"),
    re.compile(r"/group/(\d{6,25})(?:[/?#]|$)"),
    re.compile(r"/w/(\d{6,25})(?:[/?#]|$)"),
    re.compile(r"/a(\d{12,25})(?:[/?#]|$)"),
)


def public_hosts() -> frozenset[str]:
    """允许采集的公开主机;config.metrics.public_pages.hosts 可调整,默认只认头条官方域名。"""
    raw = ((cfg.load_config().get("metrics", {}) or {}).get("public_pages", {}) or {}).get("hosts")
    if not raw:
        return PUBLIC_HOSTS
    if isinstance(raw, str):
        raw = [raw]
    hosts = {str(name).strip().lower() for name in raw if str(name).strip()}
    return frozenset(hosts) or PUBLIC_HOSTS


def host_allowed(url: str) -> bool:
    """只有公开可访问的头条文章主机;子域/仿冒域一律拒绝。"""
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return host in public_hosts()


def parse_count_text(text: str | None) -> int | None:
    """"437"→437,"1,234"→1234,"1.2万"→12000;"赞"/空/负数/区间 ⇒ None(不许猜)。"""
    raw = str(text or "").strip().replace("\u00a0", " ").replace(" ", "")
    if not raw:
        return None
    match = _COUNT_RE.match(raw)
    if not match:
        return None
    number, unit = match.group(1), match.group(2)
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    if value < 0:
        return None
    multiplier = 10_000 if unit in ("万", "w", "W") else (1_000 if unit in ("k", "K") else 1)
    return int(round(value * multiplier))


def article_id_from_url(url: str | None) -> str | None:
    """从头条文章 URL 里取文章 id;取不到返回 None(绝不猜)。

    实测形态:``/article/<id>/``、``/group/<id>/``、``/w/<id>/``、m 站 ``/a<id>/``。
    栏目页 ``/ch/news_tech/`` 之类没有 id ⇒ None ⇒ 内嵌计数一律不用。
    """
    path = urlsplit(str(url or "")).path
    for pattern in _ARTICLE_ID_PATTERNS:
        match = pattern.search(path)
        if match:
            return match.group(1)
    return None


def _render_payload(page: Any) -> tuple[dict | None, str | None]:
    """取 ``<script id="RENDER_DATA">`` 里的 JSON(实测为 percent-encoded);返回 (payload, 错误标记)。"""
    raw: str | None = None
    try:
        nodes = page.css(f"#{RENDER_SCRIPT_ID}")
    except Exception:  # noqa: BLE001 - 不同页面类型兜底
        nodes = []
    for node in nodes:
        text = str(getattr(node, "text", "") or "").strip()
        if text:
            raw = text
            break
    if raw is None:
        html = str(getattr(page, "html_content", "") or "")
        match = _RENDER_BLOCK_RE.search(html)
        if not match:
            return None, None
        raw = match.group(1)
    raw = str(raw or "").strip()
    if not raw:
        return None, "empty"
    try:
        data = json.loads(raw)
    except ValueError:
        try:
            data = json.loads(urllib.parse.unquote(raw))
        except ValueError:
            return None, "bad_json"
    if not isinstance(data, dict):
        return None, "not_object"
    return data, None


def _walk_counter_blocks(node: Any, near_id: str | None, out: list[tuple[str | None, dict]], depth: int = 0) -> None:
    """收集 (最近的文章 id, itemCounter 字典);深度/数量兜底,防止畸形 JSON 撑爆。"""
    if depth > 24 or len(out) > 8:
        return
    if isinstance(node, dict):
        ident = near_id
        for key in _ID_KEYS:
            value = node.get(key)
            if value not in (None, "", 0):
                ident = str(value)
                break
        counter = node.get("itemCounter")
        if isinstance(counter, dict) and any(key in counter for key in _COUNTER_FIELD):
            clean = {
                key: value
                for key, value in counter.items()
                if isinstance(value, int) and not isinstance(value, bool)
            }
            if clean:
                out.append((ident, clean))
        for value in node.values():
            _walk_counter_blocks(value, ident, out, depth + 1)
    elif isinstance(node, list):
        for item in node[:40]:
            _walk_counter_blocks(item, near_id, out, depth + 1)


def _apply_counters(counters: dict, metrics: dict[str, int | None], sink: dict | None) -> None:
    """把已证实语义的四个计数写进指标;展现量 < 阅读量 ⇒ 拒绝 impressions(物理不可能)。"""
    for key, field in _COUNTER_FIELD.items():
        value = counters.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            continue
        metrics[field] = value
    reads, impressions = metrics["reads"], metrics["impressions"]
    if impressions is not None and reads is not None and impressions < reads:
        metrics["impressions"] = None
        if sink is not None:
            sink["sanity_rejected"] = int(sink.get("sanity_rejected", 0)) + 1


def _count_near(text: str, keywords: tuple[str, ...]) -> int | None:
    """在 "点赞437" 或 "打开评论面板，47评论" 这类文本里取数(关键词前后都允许)。"""
    body = str(text or "")
    number = r"([+]?\d[\d,]*(?:\.\d+)?[万wkW]?)"
    for keyword in keywords:
        for pattern in (rf"{keyword}\s*[:：]?\s*{number}", rf"{number}\s*{keyword}"):
            match = re.search(pattern, body)
            if match:
                value = parse_count_text(match.group(1))
                if value is not None:
                    return value
    return None


def _element_text(element: Any) -> str:
    try:
        parts = element.css("::text").getall() or []
    except Exception:  # noqa: BLE001 - 不同页面类型兜底
        parts = [element.text or ""]
    return " ".join(str(part).strip() for part in parts if str(part or "").strip())


def _first_count(scope: Any, selector: str, keywords: tuple[str, ...]) -> int | None:
    """先读 aria-label(机器可读、归属明确),再读元素文本;评论区点赞一律排除。"""
    for element in scope.css(selector):
        classes = str(element.attrib.get("class") or "")
        if "comment-like" in classes:
            continue
        value = _count_near(str(element.attrib.get("aria-label") or ""), keywords)
        if value is not None:
            return value
    for element in scope.css(selector):
        classes = str(element.attrib.get("class") or "")
        if "comment-like" in classes:
            continue
        value = parse_count_text(_element_text(element))
        if value is not None:
            return value
    return None


def _own_reads(page: Any) -> int | None:
    """本文章阅读数:只看文章头部容器,推荐卡片里的 "N阅读" 永远不算本文章的。"""
    for selector in _READ_SELECTORS:
        for element in page.css(selector):
            text = _element_text(element)
            if "阅读" not in text:
                continue
            value = _count_near(text, ("阅读",))
            if value is not None:
                return value
    return None


def extract_public_metrics(page: Any, url: str | None = None, detail: dict | None = None) -> dict[str, int | None]:
    """本文章指标:优先页面内嵌 RENDER_DATA 计数(id 必须与本页 URL 对上),再退 DOM 兜底。

    ``detail``(可选)吸收诊断信息:render_used / render_counters / render_mismatch /
    render_unattributed / render_error / sanity_rejected。返回值仍只有五个指标字段。
    """
    metrics: dict[str, int | None] = {field: None for field in METRIC_FIELDS}
    sink = detail if isinstance(detail, dict) else None

    payload, render_error = _render_payload(page)
    if render_error and sink is not None:
        sink["render_error"] = render_error
    if payload is not None:
        blocks: list[tuple[str | None, dict]] = []
        _walk_counter_blocks(payload, None, blocks)
        if sink is not None:
            sink["render_blocks"] = len(blocks)
        our_id = article_id_from_url(url) if url else None
        matched = [counters for ident, counters in blocks if our_id and ident and str(ident) == our_id]
        if matched:
            _apply_counters(matched[0], metrics, sink)
            if sink is not None:
                sink["render_used"] = True
                sink["render_counters"] = dict(matched[0])
        elif blocks:
            unattributed = sum(1 for ident, _counters in blocks if not ident)
            if sink is not None:
                if unattributed == len(blocks):
                    sink["render_unattributed"] = int(sink.get("render_unattributed", 0)) + unattributed
                else:
                    sink["render_mismatch"] = int(sink.get("render_mismatch", 0)) + (len(blocks) - unattributed)

    toolbars = page.css(_TOOLBAR_SELECTOR)
    if toolbars and (metrics["likes"] is None or metrics["comments"] is None):
        block = toolbars[0]
        if metrics["likes"] is None:
            metrics["likes"] = _first_count(block, ".detail-like", ("点赞",))
        if metrics["comments"] is None:
            metrics["comments"] = _first_count(block, ".detail-interaction-comment", ("评论",))
    if metrics["comments"] is None:
        for element in page.css("#comment-area .title"):
            value = _count_near(_element_text(element), ("评论",))
            if value is not None:
                metrics["comments"] = value
                break
    if metrics["reads"] is None:
        metrics["reads"] = _own_reads(page)
    return metrics


def _candidates(
    category: str, db_path: Path | None, limit: int | None, retry_gone: bool = False
) -> tuple[list[dict], int, list[int]]:
    """挑本轮要采的文章:没测过的先采,都要采时最久没刷的先刷。

    只按 id 倒序取前 N 篇会反复采同一批新文章,标定样本永远攒不到
    ``viral.MIN_CALIBRATION_SAMPLE`` ⇒ 必须让每一轮都往前推进(P22 根因修复)。

    P34 ③:台账里仍在有效期内、且状态是 404/410(内容确认不存在)的文章**不再发请求**,
    只计 ``known_gone``;``retry_gone=True`` 用于事后核查,强制重新取证。
    返回 ``(待采清单, 域名不在白名单而不可采的文章数, 已确认下架而跳过的文章 id)``。

    注意口径:``limit`` 只限制 ``picked`` 的长度,**不限制扫描范围**。
    两个跳过计数必须是对整个类目库的统计,否则 ``--limit`` 一小就会报 0,
    "本轮到底少发了多少请求"就没法核对(实测过这个坑:limit=2 时旧写法报 0)。
    """
    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    con = connect(target)
    try:
        rows = con.execute(
            "SELECT a.id, a.url, a.url_normalized, m.captured_at"
            " FROM articles a LEFT JOIN article_metrics m ON m.article_id = a.id"
            " ORDER BY (m.captured_at IS NULL) DESC, m.captured_at ASC, a.id DESC"
        ).fetchall()
        link_status = {} if retry_gone else _read_link_status(con)
    finally:
        con.close()
    now = datetime.now().astimezone()
    picked: list[dict] = []
    host_skipped = 0
    known_gone: list[int] = []
    capped = limit is not None and int(limit) <= 0
    for row in rows:
        url = str(row["url_normalized"] or row["url"] or "")
        if not url or not host_allowed(url):
            host_skipped += 1
            continue
        if _status_fresh(link_status.get(str(int(row["id"]))) or {}, now):
            known_gone.append(int(row["id"]))
            continue
        if capped or (limit is not None and len(picked) >= int(limit)):
            continue  # 已经挑够了:这一行不再入队,但扫描继续,计数保持全库口径
        picked.append({"article_id": int(row["id"]), "url": url})
    return picked, host_skipped, known_gone


def collect_public_metrics(
    category: str,
    db_path: Path | None = None,
    limit: int | None = 20,
    apply: bool = False,
    fetch_page: Callable[[str], Any] | None = None,
    authorized: dict[str, str] | None = None,
    rules_provider: Callable[[str], robots.RobotsRules] | None = None,
    sleep_s: float | None = None,
    sleeper: Callable[[float], None] | None = None,
    captured_at: str | None = None,
    source_id: str = SOURCE_ID,
    retry_gone: bool = False,
) -> dict:
    """采集头条公开文章页互动数。默认 dry-run;``apply=True`` 才写库。

    ``retry_gone=True``:忽略"已确认下架"台账,强制重新取证(事后核查用,默认 False)。
    """
    allow = authorized if authorized is not None else metrics_import.load_authorized_sources()
    if source_id not in allow:
        raise PermissionError(
            f"未授权数据来源 {source_id!r}:必须先在 config.metrics.authorized_sources 登记,"
            "否则拒绝采集入库(防止未经登记的来源污染指标)。"
        )
    candidates, host_skipped, known_gone = _candidates(category, db_path, limit, retry_gone=retry_gone)
    report: dict[str, Any] = {
        "category": category,
        "source": source_id,
        "candidates": len(candidates),
        "host_skipped": host_skipped,
        # P34 ③:已确认下架(404/410)且在有效期内的文章 ⇒ 本轮不再发请求,单独计数不混进失败
        "known_gone_skipped": len(known_gone),
        "known_gone_ids": known_gone,
        "retry_gone": bool(retry_gone),
        "link_status_recorded": 0,
        "link_status_cleared": 0,
        "robots_refused": 0,
        "fetched": 0,
        "fetch_errors": 0,
        "no_metrics_skipped": 0,
        "render_ok": 0,
        "render_mismatch": 0,
        "render_unattributed": 0,
        "render_errors": 0,
        "sanity_rejected": 0,
        "written": 0,
        "updated": 0,
        "snapshots": 0,
        "filled_from_null": 0,
        "protected_by_credibility": 0,
        "null_kept": 0,
        "dry_run": not apply,
        "pages": [],
        "outcomes": [],
        "outcome_counts": {},
        "changes": [],
        "row_errors": [],
    }
    if not candidates:
        return report

    crawler_cfg = cfg.load_config().get("crawler", {})
    user_agent = str(crawler_cfg.get("user_agent", "TouTiaoSmartFactory/0.1"))
    delay = float(sleep_s if sleep_s is not None else crawler_cfg.get("min_delay_s", 1.0) or 0)
    nap = sleeper or time.sleep
    captured = captured_at or cfg.now_iso()
    rules_cache: dict[str, robots.RobotsRules] = {}
    rows: list[dict] = []

    for candidate in candidates:
        url = candidate["url"]
        outcome: dict[str, Any] = {
            "article_id": candidate["article_id"],
            "url": url,
            "outcome": None,
            "status": None,
            "reason": None,
            "fetched": False,
            "metrics_written": False,
            "observed": {},
        }
        parts = urlsplit(url)
        base = f"{parts.scheme or 'https'}://{parts.netloc}/"
        rules = rules_cache.get(base)
        if rules is None:
            rules = (
                rules_provider(base)
                if rules_provider is not None
                else robots.fetch_robots(base, user_agent)
            )
            rules_cache[base] = rules
        if rules.unknown or not rules.allowed(robots.target_path(url)):
            report["robots_refused"] += 1
            LOG.warn("metrics.public.robots_refused", url=url)
            outcome["outcome"] = OUTCOME_ROBOTS_REFUSED
            outcome["reason"] = (
                "robots.txt 取不到,按不抓取处理" if rules.unknown
                else f"robots 禁止抓取 {robots.target_path(url)}"
            )
            _record_outcome(report, outcome)
            continue
        if report["fetched"] and delay > 0:
            nap(delay)
        try:
            page = fetch_page(url)
        except Exception as exc:  # noqa: BLE001 - 单页失败不影响整批
            report["fetch_errors"] += 1
            classified, status = _classify_fetch_failure(exc)
            outcome["outcome"] = classified
            outcome["status"] = status
            outcome["reason"] = str(exc)[:200]
            if classified == OUTCOME_GONE:
                # 文章已失效:不写指标、不写 0,只留可解释状态(P25 那 4 条 404 的根因归到这里)
                LOG.warn("metrics.public.article_gone", url=url, status=status, error=str(exc)[:200])
            elif classified == OUTCOME_LEGAL_BLOCKED:
                # 451:因法律/政策要求不可用 ⇒ 不当下架证据,也不进跳过台账,下一轮照常复查
                LOG.warn("metrics.public.blocked_by_legal", url=url, status=status, error=str(exc)[:200])
            else:
                LOG.warn("metrics.public.fetch_error", url=url, error=str(exc)[:200])
            _record_outcome(report, outcome)
            continue
        report["fetched"] += 1
        outcome["fetched"] = True
        detail: dict[str, Any] = {}
        metrics = extract_public_metrics(page, url=url, detail=detail)
        report["render_ok"] += 1 if detail.get("render_used") else 0
        report["render_mismatch"] += int(detail.get("render_mismatch", 0))
        report["render_unattributed"] += int(detail.get("render_unattributed", 0))
        report["render_errors"] += 1 if detail.get("render_error") else 0
        report["sanity_rejected"] += int(detail.get("sanity_rejected", 0))
        observed = {field: metrics[field] for field in METRIC_FIELDS if metrics[field] is not None}
        outcome["observed"] = observed
        outcome["unproven_fields"] = list(UNPROVEN_FIELDS)
        entry = {"article_id": candidate["article_id"], "url": url}
        entry.update(metrics)
        if detail.get("render_used"):
            # 只把"归属已证明"的原始计数当证据留在报告里;未对上的绝不附在本文章条目上
            entry["render_counters"] = detail.get("render_counters") or {}
        report["pages"].append(entry)
        if not observed:
            report["no_metrics_skipped"] += 1
            LOG.warn("metrics.public.no_counts", url=url)
            outcome["outcome"] = OUTCOME_NO_COUNTS
            outcome["reason"] = "页面里没有属于本文章的可归属计数"
            _record_outcome(report, outcome)
            continue
        outcome["outcome"] = OUTCOME_OK
        _record_outcome(report, outcome)
        rows.append({"article_id": candidate["article_id"], **metrics, "captured_at": captured})

    if rows and apply:
        written = metrics_import.write_metric_rows(
            category,
            rows,
            source_id,
            db_path=db_path,
            authorized=allow,
            captured_at=captured,
        )
        for key in (
            "written",
            "updated",
            "snapshots",
            "filled_from_null",
            "protected_by_credibility",
            "null_kept",
        ):
            report[key] = written.get(key, 0)
        report["changes"] = written.get("changes", [])
        report["row_errors"] = written.get("row_errors", [])
        written_ids = {entry["article_id"] for entry in report["changes"]}
        for outcome in report["outcomes"]:
            outcome["metrics_written"] = outcome["article_id"] in written_ids
    elif rows:
        report["would_write"] = len(rows)

    # P34 ③:下架台账只在 apply=True 时更新(dry-run 不落任何状态)
    _update_link_status(category, db_path, report, apply, captured)

    LOG.info(
        "metrics.public.summary",
        category=category,
        candidates=report["candidates"],
        fetched=report["fetched"],
        robots_refused=report["robots_refused"],
        fetch_errors=report["fetch_errors"],
        no_metrics=report["no_metrics_skipped"],
        known_gone_skipped=report["known_gone_skipped"],
        link_status_recorded=report["link_status_recorded"],
        outcome_counts=report["outcome_counts"],
        applied=apply,
    )
    return report


def collect_with_browser(
    category: str,
    db_path: Path | None = None,
    limit: int | None = 20,
    apply: bool = False,
    **kwargs: Any,
) -> dict:
    """正式入口:整次采集共用一个浏览器会话(正常渲染,不登录、不反检测)。"""
    with fetcher.browser_session() as session:

        def fetch(url: str) -> Any:
            return fetcher.fetch_page(url, render=True, session=session).page

        return collect_public_metrics(
            category,
            db_path=db_path,
            limit=limit,
            apply=apply,
            fetch_page=fetch,
            **kwargs,
        )
