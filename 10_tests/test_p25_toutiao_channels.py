"""P25(第 8 轮,2026-11-16)测试:把**取证过**的头条 finance / history 频道注册成正式来源。

为什么要有这批断言(根因账见 `08_wiki/reports/P24-metrics-and-prediction-loop-20261116.md` 第一、六节):
其余 5 个类目 `article_metrics` 恒 0,不是指标命令坏了,而是那些类目语料里**没有头条文章**
—— 而 `metrics_pages._candidates` 只按 `host_allowed(url)` 选候选。要补指标,先得有能产出
头条文章的来源。上一轮两级取证:静态抓频道页只有 4827 字节 JS 外壳(0 条逐篇链接),用生产适配器
(`fetcher.browser_session()` + `toutiao.collect(limit=3)`)复探才拿到真链接与正文:
`pages 4 / links 17 / items 3 / errors 0`,正文 1450–2985 字,发布到 11-16 06:26/07:15
(凭据 `07_tmp/r27_metrics_expand/probe_channels_out.txt`、`probe_channels_browser_out.txt`)。
所以这里**钉死取证过的事实**:URL slug 一字不差(不许后来的人凭猜测改)、走生产头条适配器与浏览器渲染、
限速与补全预算达标、并且产出的文章会落进指标白名单(否则闭环又断在 `host_skipped`)。
"""

from __future__ import annotations

from ttxt.crawler import metrics_pages, runner, sources

# 取证过的两个频道类目 → 来源 id 与 URL(slug 由 probe_channels_out.txt 证明存在)
PROVEN = {
    "toutiao_finance": ("finance", "https://www.toutiao.com/ch/news_finance/"),
    "toutiao_history": ("history", "https://www.toutiao.com/ch/news_history/"),
}


def _loaded() -> dict[str, sources.Source]:
    return {source.id: source for source in sources.load_sources()}


def test_proven_channels_are_registered():
    loaded = _loaded()
    for source_id, (category, url) in PROVEN.items():
        assert source_id in loaded, f"未注册取证过的头条频道来源 {source_id}"
        source = loaded[source_id]
        assert source.category == category
        assert source.url == url, "频道 slug 必须与取证件一致,不得凭猜测改写"
        assert source.extractor == "toutiao", "必须走生产头条适配器(通用抽取器抓不到 JS 外壳)"
        assert source.render == "browser", "头条频道页是 JS 渲染,静态 http 抓不到逐篇链接"
        assert source.enabled is True
        assert source.demo is False, "正式类目不许挂在演示来源上"
        assert source.rate_limit_s >= 2.0, "头条逐请求限速不得低于 2s(与 toutiao_tech 同档)"
        assert source.max_item_pages >= source.max_articles, "P20 规则:补正文预算不得小于入库配额"
        assert "取证" in source.notes and "robots" in source.notes, "来源必须写清取证依据与访问控制依据"


def test_channel_source_passes_category_routing():
    """采集路由:新来源必须解析到已存在的类目命名空间,否则 job 会被判 routing_rejected。"""
    loaded = _loaded()
    for source_id in PROVEN:
        category = runner.resolve_category(loaded[source_id], None)
        assert category == PROVEN[source_id][0]


def test_articles_from_these_sources_are_metric_candidates():
    """闭环关键耦合:头条频道产出的文章地址必须落在指标主机白名单里,不能又断在 host_skipped。"""
    loaded = _loaded()
    for source_id in PROVEN:
        assert metrics_pages.host_allowed(loaded[source_id].url)
        # 上一轮真实抓到的文章地址(取证件里的样本),必须同样被白名单接纳
        assert metrics_pages.host_allowed("https://www.toutiao.com/article/7708540714849763876/")


def test_registered_channels_are_schedulable():
    """调度器 `crawl_enqueue` 只认 `enabled`;两个新来源必须进入每轮入队清单。"""
    enabled = [source.id for source in sources.load_sources() if source.enabled]
    for source_id in PROVEN:
        assert source_id in enabled
