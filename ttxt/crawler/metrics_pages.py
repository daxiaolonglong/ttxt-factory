"""P21:头条公开文章页互动指标采集(只采本文章自己的点赞/评论)。

取证依据(07_tmp/r24_metrics_probe/,本轮实测):
- probe1.json:头条 robots.txt 不禁 ``/article/``;但纯 HTTP 抓到的是字节跳动 JSVMP 反爬壳
  (三条不同文章 URL 的响应字节数完全一样,正文与文章 id 都不出现)⇒ 只能走项目正常浏览器渲染通道;
- probe2_render.json / probe3_selectors.json:渲染后的页面里,**本文章**只有
  ``.detail-side-interaction .detail-like[aria-label="点赞N"]`` 与
  ``.detail-interaction-comment[aria-label="…N评论"]``(兜底 ``#comment-area .title`` 的 "评论 N");
  "16万阅读" 这类数字出现在"推荐阅读"卡片里(**那是别的文章的**阅读数),
  收藏数与展现量公开页面根本没有。

诚实边界(07_rules/CORE_RULES.md 规则 41):
- 主机白名单 + 逐 URL robots 闸门:不放行 ⇒ 一次抓取都不许发生;
- 只取本文章容器内的计数;推荐卡片、别人评论的点赞数一律不算本文章的指标;
- 取不到 ⇒ NULL,绝不折算 0;页面显示真 0 ⇒ 如实写 0;
  reads / favorites / impressions 公开页没有 ⇒ 恒为 NULL(缺证据 ≠ 表现差);
- 默认 dry-run(只报告不落库),``apply=True`` 才写;写库走 metrics_import 的可信度合并 + 时序快照。
"""

from __future__ import annotations

import re
import time
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


def extract_public_metrics(page: Any) -> dict[str, int | None]:
    """只取本文章工具栏的点赞/评论(兜底本文章评论区标题);其余字段 None。"""
    metrics: dict[str, int | None] = {field: None for field in METRIC_FIELDS}
    toolbars = page.css(_TOOLBAR_SELECTOR)
    if toolbars:
        block = toolbars[0]
        metrics["likes"] = _first_count(block, ".detail-like", ("点赞",))
        metrics["comments"] = _first_count(block, ".detail-interaction-comment", ("评论",))
    if metrics["comments"] is None:
        for element in page.css("#comment-area .title"):
            value = _count_near(_element_text(element), ("评论",))
            if value is not None:
                metrics["comments"] = value
                break
    metrics["reads"] = _own_reads(page)
    return metrics


def _candidates(category: str, db_path: Path | None, limit: int | None) -> tuple[list[dict], int]:
    target = Path(db_path) if db_path is not None else cfg.category_db_path(category)
    con = connect(target)
    try:
        rows = con.execute(
            "SELECT id, url, url_normalized FROM articles ORDER BY id DESC"
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
        metrics = extract_public_metrics(page)
        observed = {field: metrics[field] for field in METRIC_FIELDS if metrics[field] is not None}
        entry = {"article_id": candidate["article_id"], "url": url}
        entry.update(metrics)
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
