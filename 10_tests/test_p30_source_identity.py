# -*- coding: utf-8 -*-
"""P30:逐篇来源身份 + "第二家独立媒体"判定的回归测试(红→绿)。

背景:P29 之后黄金集 A/B 级独立证据只有 17/61 = 27.9%,而语料里跨域名同题聚类 0 组。
本人 2026-11-16 抽查 6 篇中新网原文后定下口径:
**域名不等于一手来源,转载稿不能当第二家独立媒体**。本轮把这条口径写成代码 + 测试。
凭据:07_tmp/p30_source_identity/*。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ttxt.analysis import source_identity as si

ROOT = Path(__file__).resolve().parents[1]

# 2026-11-16 17:36 实测抓自 https://www.chinanews.com.cn/sh/2026/11-15/10732336.shtml
OWN_HEADER = (
    "第三十八届韩素音国际翻译大赛颁奖仪式在京举行 2026-11-15 14:56:05 来源： 中国新闻网 "
    "作者：张子怡 责任编辑：张子怡"
)
# 抓自 …/10732322.shtml(同一模板,但外部稿)
REPRINT_HEADER = (
    "多个景区接连被降级、摘牌！A级旅游景区如何“重构”？ 2026-11-15 14:24:00 "
    "来源： 央视新闻客户端 作者：黄钰涵 责任编辑：黄钰涵"
)
DATELINE_BODY = "中新网 北京11月15日电 (记者 应妮)口译一时着急把俄罗斯“天鹅湖”说成“企鹅湖”。"
XINHUA_DATELINE_BODY = "新华社天津电 记者张三 报道,小洋楼里沉浸式入戏。"


def test_page_marker_extracts_own_agency() -> None:
    assert si.extract_page_marker(OWN_HEADER) == "中国新闻网"


def test_page_marker_extracts_external_agency() -> None:
    assert si.extract_page_marker(REPRINT_HEADER) == "央视新闻客户端"


def test_page_marker_none_when_absent() -> None:
    assert si.extract_page_marker("一段没有任何署名行的正文") is None


def test_classify_own_report_when_marker_and_dateline_agree() -> None:
    ident = si.classify(si.extract_page_marker(OWN_HEADER), si.extract_dateline(DATELINE_BODY),
                        own_agency="chinanews")
    assert ident.kind == si.KIND_OWN
    assert ident.agency == "chinanews"
    assert ident.evidence  # 必须留下依据片段,不许只给结论


def test_classify_reprint_names_the_external_agency() -> None:
    ident = si.classify(si.extract_page_marker(REPRINT_HEADER), None, own_agency="chinanews")
    assert ident.kind == si.KIND_REPRINT
    assert ident.agency == "cctv_news"
    assert ident.page_marker == "央视新闻客户端"


def test_classify_unknown_when_no_marker_no_dateline() -> None:
    ident = si.classify(None, None, own_agency="chinanews")
    assert ident.kind == si.KIND_UNKNOWN
    assert ident.agency is None


def test_conflicting_byline_is_not_trusted_as_own() -> None:
    """页面标「中国新闻网」但正文电头是新华社 ⇒ 不得当作中新网自己的报道。"""
    ident = si.classify("中国新闻网", si.extract_dateline(XINHUA_DATELINE_BODY), own_agency="chinanews")
    assert ident.kind == si.KIND_CONFLICT
    assert ident.agency == "xinhua"  # 电头优先,域名让路


def test_dateline_detects_xinhua_and_chinanews() -> None:
    assert si.extract_dateline(XINHUA_DATELINE_BODY) == "新华社"
    assert si.extract_dateline(DATELINE_BODY) == "中新网"
    assert si.extract_dateline("今天(15日),北方地区进入全面供暖季。") is None


def test_two_newsrooms_are_independent_but_reprint_is_not() -> None:
    eastmoney = si.Identity(kind=si.KIND_OWN, agency="eastmoney", page_marker=None,
                            dateline_agency=None, evidence="registered source")
    tmtpost = si.Identity(kind=si.KIND_OWN, agency="tmtpost", page_marker=None,
                          dateline_agency=None, evidence="registered source")
    chinanews_own = si.Identity(kind=si.KIND_OWN, agency="chinanews", page_marker="中国新闻网",
                                dateline_agency="中新网", evidence=OWN_HEADER)
    cctv_reprint = si.Identity(kind=si.KIND_REPRINT, agency="cctv_news", page_marker="央视新闻客户端",
                               dateline_agency=None, evidence=REPRINT_HEADER)
    chinanews_reprint = si.Identity(kind=si.KIND_REPRINT, agency="chinanews",
                                    page_marker="中国新闻网", dateline_agency=None, evidence="转载稿")
    unknown = si.Identity(kind=si.KIND_UNKNOWN, agency=None, page_marker=None,
                          dateline_agency=None, evidence="无署名")
    assert si.independent(eastmoney, tmtpost) is True
    assert si.independent(chinanews_own, chinanews_reprint) is False
    assert si.independent(chinanews_own, cctv_reprint) is True
    assert si.independent(tmtpost, tmtpost) is False
    assert si.independent(eastmoney, unknown) is False, "身份不明不得假定独立"


def test_aggregator_articles_never_count_as_independent_second_source() -> None:
    """今日头条是聚合平台,自媒体稿可能是转载稿 ⇒ 没查到血统前一律不算第二家独立媒体。"""
    chinanews_own = si.Identity(kind=si.KIND_OWN, agency="chinanews", page_marker="中国新闻网",
                                dateline_agency="中新网", evidence=OWN_HEADER)
    toutiao = si.Identity(kind=si.KIND_AGGREGATOR, agency="toutiao", page_marker=None,
                          dateline_agency=None, evidence="聚合平台自媒体")
    assert si.independent(chinanews_own, toutiao) is False
    assert si.independent(toutiao, chinanews_own) is False


def test_event_pairs_reject_same_agency_and_low_overlap() -> None:
    items = [
        {"url": "https://a.eastmoney.com/x", "title": "央行宣布下调存款准备金率0.5个百分点",
         "agency": "eastmoney", "domain": "eastmoney.com"},
        {"url": "https://b.tmtpost.com/y", "title": "中国人民银行下调存款准备金率0.5个百分点",
         "agency": "tmtpost", "domain": "tmtpost.com"},
        {"url": "https://c.tmtpost.com/z", "title": "央行宣布下调存款准备金率0.5个百分点",
         "agency": "tmtpost", "domain": "tmtpost.com"},
        {"url": "https://d.ifanr.com/w", "title": "苹果发布新款 MacBook Pro 搭载 M5 芯片",
         "agency": "ifanr", "domain": "ifanr.com"},
    ]
    ids = {it["url"]: si.Identity(kind=si.KIND_OWN, agency=it["agency"], page_marker=None,
                                  dateline_agency=None, evidence="registered source")
           for it in items}
    pairs, rejected = si.find_pairs(items, ids)
    matched = {(p["left"]["domain"], p["right"]["domain"]) for p in pairs}
    assert ("eastmoney.com", "tmtpost.com") in matched, "两家独立新闻机构同题必须成对"
    assert all("tmtpost.com" != p["left"]["domain"] or "tmtpost.com" != p["right"]["domain"] for p in pairs)
    assert rejected, "同机构/低相似必须被拒并留下理由"
    assert all(r["reason"] for r in rejected)


def test_inventory_row_schema_is_auditable() -> None:
    row = si.inventory_row(category="social", article_id=2, url="https://www.chinanews.com.cn/x.shtml",
                           title="多个景区接连被降级、摘牌", marker="央视新闻客户端",
                           dateline=None, identity=si.classify("央视新闻客户端", None),
                           probed_at="2026-11-16T17:36:00+08:00")
    for key in ("category", "article_id", "url", "title", "page_marker", "dateline_agency",
                "kind", "agency", "independent_eligible", "is_first_hand_of_event", "evidence",
                "probed_at"):
        assert key in row, f"清单缺字段 {key}"
    assert row["kind"] == si.KIND_REPRINT
    assert row["independent_eligible"] is True, "外部新闻机构本身可以作为独立一家"
    assert row["is_first_hand_of_event"] is False, "转载稿不是事件的一手证据"


def test_inventory_asset_exists_and_is_honest() -> None:
    """落盘的逐篇清单必须存在、字段齐、不许出现没有依据就标 own 的行。"""
    path = ROOT / "03_intelligence" / "source_identity" / "chinanews-20261116.jsonl"
    assert path.exists(), "逐篇来源身份清单还没生成"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 50, f"中新网在库 53 篇,清单只有 {len(rows)} 行"
    kinds = {r["kind"] for r in rows}
    assert kinds <= {si.KIND_OWN, si.KIND_REPRINT, si.KIND_UNKNOWN, si.KIND_CONFLICT}
    for r in rows:
        assert r["url"].startswith("https://www.chinanews.com.cn/"), "清单只许收录中新网在库文章"
        if r["kind"] == si.KIND_REPRINT:
            assert r["agency"] and r["agency"] != "chinanews", "转载稿必须写明真正发布者"
        if r["kind"] == si.KIND_OWN:
            assert r["evidence"], "标 own 必须有页面署名或电头依据"
    counts = {k: sum(1 for r in rows if r["kind"] == k) for k in kinds}
    assert counts.get(si.KIND_UNKNOWN, 0) + counts.get(si.KIND_REPRINT, 0) > 0, (
        "全是 own 说明没真去看页面")
    assert all(r["probed_at"] for r in rows)


def test_every_registered_source_declares_its_publisher() -> None:
    """T-02 扩源:每个注册来源必须显式声明发布机构。

    漏声明会走 ``canon_agency(source_id)`` 回退,把 source_id 当成机构名 ⇒ 聚合平台
    (如 toutiao_regimen)会被误判成 "own_report",进而和真媒体配成假独立对。
    这条把"漏声明即红"钉死,新增来源时必须同步 SOURCE_PUBLISHER。
    """
    raw = json.loads((ROOT / "02_crawler" / "sources.json").read_text(encoding="utf-8"))
    missing = [s["id"] for s in raw["sources"] if s["id"] not in si.SOURCE_PUBLISHER]
    assert missing == [], f"这些来源没有在 SOURCE_PUBLISHER 里声明发布机构: {missing}"


def test_toutiao_regimen_is_aggregator_not_independent() -> None:
    """health 频道的头条源与其它 toutiao_* 一样是聚合平台,不得当独立第二来源。"""
    regimen = si.identity_for_source("toutiao_regimen")
    assert regimen.kind == si.KIND_AGGREGATOR
    assert regimen.agency == "toutiao"
    who = si.identity_for_source("who_cn_news")
    assert si.independent(regimen, who) is False


def test_ithome_dateline_marks_own_report_and_reprint_of_ithome() -> None:
    """「IT之家 11 月 18 日消息」既能证 IT之家自有稿,也会把转载方正确指回 ithome。"""
    body = "IT之家 11 月 18 日消息，据美国 CNBC 援引知情人士消息称，Anthropic 考虑扩大 IPO 规模。"
    assert si.extract_dateline(body) == "IT之家"
    own = si.classify(None, si.extract_dateline(body), own_agency="ithome")
    assert own.kind == si.KIND_OWN and own.agency == "ithome"
    # 东方财富转载同一句电头时,agency 也是 ithome ⇒ 与 IT之家不构成两家独立媒体
    reprint = si.classify(None, si.extract_dateline(body), own_agency="eastmoney")
    assert reprint.kind == si.KIND_REPRINT and reprint.agency == "ithome"
    assert si.independent(own, reprint) is False


def test_pair_candidate_pages_show_reprints_are_being_caught() -> None:
    """财经跨机构候选对的逐页复核:必须抓到转载稿/无署名,不许默认当自有报道。

    2026-11-16 17:53 实测(需浏览器渲染,静态抓取被跳转到本机地址的防机器人响应挡住):
    东方财富 3 篇里 2 篇是转载(财联社 / 国是直通车),钛媒体那篇页头无署名。
    这一条把"域名≠一手来源"钉成回归。
    """
    path = ROOT / "03_intelligence" / "source_identity" / "finance-pairs-20261116.jsonl"
    assert path.exists(), "候选对的逐页署名复核清单还没生成"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 4
    assert not any(r["kind"] == si.KIND_AGGREGATOR for r in rows)
    caught = [r for r in rows if r["kind"] in (si.KIND_REPRINT, si.KIND_UNKNOWN)]
    assert len(caught) >= 2, f"逐页复核没抓到任何转载/无署名,怀疑按域名臆断:{[r['kind'] for r in rows]}"
    for r in rows:
        assert r["evidence"], "每一行都要留下判定依据"
