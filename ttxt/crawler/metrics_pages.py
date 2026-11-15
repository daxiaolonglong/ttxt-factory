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


def _candidates(category: str, db_path: Path | None, limit: int | None) -> tuple[list[dict], int]:
    """挑本轮要采的文章:没测过的先采,都要采时最久没刷的先刷。

    只按 id 倒序取前 N 篇会反复采同一批新文章,标定样本永远攒不到
    ``viral.MIN_CALIBRATION_SAMPLE`` ⇒ 必须让每一轮都往前推进(P22 根因修复)。
    """
    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    con = connect(target)
    try:
        rows = con.execute(
            "SELECT a.id, a.url, a.url_normalized, m.captured_at"
            " FROM articles a LEFT JOIN article_metrics m ON m.article_id = a.id"
            " ORDER BY (m.captured_at IS NULL) DESC, m.captured_at ASC, a.id DESC"
        ).fetchall()
    finally:
        con.close()
    picked: list[dict] = []
    host_skipped = 0
    for row in rows:
        url = str(row["url_normalized"] or row["url"] or "")
        if not url or not host_allowed(url):
            host_skipped += 1
            continue
        picked.append({"article_id": int(row["id"]), "url": url})
        if limit is not None and len(picked) >= int(limit):
            break
    return picked, host_skipped


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
) -> dict:
    """采集头条公开文章页互动数。默认 dry-run;``apply=True`` 才写库。"""
    allow = authorized if authorized is not None else metrics_import.load_authorized_sources()
    if source_id not in allow:
        raise PermissionError(
            f"未授权数据来源 {source_id!r}:必须先在 config.metrics.authorized_sources 登记,"
            "否则拒绝采集入库(防止未经登记的来源污染指标)。"
        )
    candidates, host_skipped = _candidates(category, db_path, limit)
    report: dict[str, Any] = {
        "category": category,
        "source": source_id,
        "candidates": len(candidates),
        "host_skipped": host_skipped,
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
            continue
        if report["fetched"] and delay > 0:
            nap(delay)
        try:
            page = fetch_page(url)
        except Exception as exc:  # noqa: BLE001 - 单页失败不影响整批
            report["fetch_errors"] += 1
            LOG.warn("metrics.public.fetch_error", url=url, error=str(exc)[:200])
            continue
        report["fetched"] += 1
        detail: dict[str, Any] = {}
        metrics = extract_public_metrics(page, url=url, detail=detail)
        report["render_ok"] += 1 if detail.get("render_used") else 0
        report["render_mismatch"] += int(detail.get("render_mismatch", 0))
        report["render_unattributed"] += int(detail.get("render_unattributed", 0))
        report["render_errors"] += 1 if detail.get("render_error") else 0
        report["sanity_rejected"] += int(detail.get("sanity_rejected", 0))
        observed = {field: metrics[field] for field in METRIC_FIELDS if metrics[field] is not None}
        entry = {"article_id": candidate["article_id"], "url": url}
        entry.update(metrics)
        if detail.get("render_used"):
            # 只把"归属已证明"的原始计数当证据留在报告里;未对上的绝不附在本文章条目上
            entry["render_counters"] = detail.get("render_counters") or {}
        report["pages"].append(entry)
        if not observed:
            report["no_metrics_skipped"] += 1
            LOG.warn("metrics.public.no_counts", url=url)
            continue
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
        report["row_errors"] = written.get("row_errors", [])
    elif rows:
        report["would_write"] = len(rows)

    LOG.info(
        "metrics.public.summary",
        category=category,
        candidates=report["candidates"],
        fetched=report["fetched"],
        robots_refused=report["robots_refused"],
        fetch_errors=report["fetch_errors"],
        no_metrics=report["no_metrics_skipped"],
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
