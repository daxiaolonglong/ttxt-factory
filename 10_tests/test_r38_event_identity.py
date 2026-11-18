# -*- coding: utf-8 -*-
"""R38:结构化「事件同一性」通路(同日 + 核心实体 + 数字)的回归测试。

背景:R37 实测标题相似度 0.30 阈值漏掉 5 对真同事件,但下调阈值会连带放行
「同题不同事」假对。项目负责人裁定新增结构化通路,并要求:
转载/同源/官方通稿优先级更高(不得绕过)、对照集 0 误配、新旧两套并行读数。

本测试钉死四件事:
1. 结构化通路只在同日、具名核心实体 ≥2、数字 ≥2 且重合率 ≥0.5 时放行;
2. 同域名 / 同机构 / 正文 5-gram 重合 ≥0.45(同稿转载)一律先否决;
3. 缺正文、缺日期不判同;
4. 旧标题通路(``MIN_EVENT_SCORE``/``event_score``/``find_pairs``)行为不变。
"""
from __future__ import annotations

from ttxt.analysis import event_identity as ei
from ttxt.analysis import source_identity as si

LEFT_TEXT = (
    "中指研究院发布11月楼市报告。刘水表示，11月百城新房均价环比下跌0.83%，"
    "二手住宅挂牌量升至1200万套。多地优化限购政策，市场成交仍在低位徘徊。"
)
RIGHT_TEXT = (
    "中指研究院近日发布数据。刘水表示，11月百城新房价格环比降0.83%，"
    "二手房挂牌量1200万套，市场仍在调整。业内预计后续政策仍有加码空间。"
)


def _ident(agency: str, kind: str = si.KIND_OWN) -> si.Identity:
    return si.Identity(kind, agency, agency, None, "test")


def _item(url: str, title: str, *, date: str = "2026-11-18", text: str = LEFT_TEXT) -> dict:
    return {"url": url, "title": title, "domain": si.domain_of(url),
            "date": date, "text": text}


def _pair(left_text: str = LEFT_TEXT, right_text: str = RIGHT_TEXT, *,
          left_date: str = "2026-11-18", right_date: str = "2026-11-18",
          left_agency: str = "yicai", right_agency: str = "nbd",
          left_domain: str = "a.example.com", right_domain: str = "b.example.com") -> dict:
    left = {"url": f"https://{left_domain}/1", "title": "中指研究院：11月百城房价环比回落",
            "domain": left_domain, "date": left_date, "text": left_text}
    right = {"url": f"https://{right_domain}/2", "title": "房价继续调整，机构看年末政策窗口",
             "domain": right_domain, "date": right_date, "text": right_text}
    identities = {left["url"]: _ident(left_agency), right["url"]: _ident(right_agency)}
    return ei.judge_pair(left, right, identities)


def test_accepts_same_day_same_core_entities_and_numbers() -> None:
    verdict = _pair()
    assert verdict["accepted"] is True
    assert verdict["stage"] == "structured"
    evidence = verdict["evidence"]
    assert len(evidence["shared_core"]) >= ei.MIN_SHARED_CORE_ENTITIES
    assert len(evidence["shared_numbers"]) >= ei.MIN_SHARED_NUMBERS
    assert evidence["copy_containment"] < ei.COPY_CONTAINMENT


def test_title_similarity_is_only_auxiliary() -> None:
    """标题完全不同也必须能判同 —— 这正是 R37 漏掉的真同事件形态。"""
    verdict = _pair()
    assert verdict["title_score_aux"] < si.MIN_EVENT_SCORE
    assert verdict["accepted"] is True


def test_rejects_different_day() -> None:
    verdict = _pair(left_date="2026-11-18", right_date="2026-11-19")
    assert verdict["accepted"] is False
    assert "不同日" in verdict["reason"]


def test_rejects_missing_date() -> None:
    verdict = _pair(right_date="")
    assert verdict["accepted"] is False
    assert "缺少发布时间" in verdict["reason"]


def test_rejects_missing_body() -> None:
    verdict = _pair(right_text="   ")
    assert verdict["accepted"] is False
    assert "缺少正文" in verdict["reason"]


def test_rejects_same_domain_before_structured() -> None:
    verdict = _pair(left_domain="same.example.com", right_domain="same.example.com",
                    left_agency="yicai", right_agency="nbd")
    assert verdict["accepted"] is False
    assert verdict["stage"] == "domain"


def test_rejects_same_agency_before_structured() -> None:
    """官方通稿/同源:不同域名、不同正文措辞,只要规范机构 id 相同就不算独立。"""
    verdict = _pair(left_domain="a.example.com", right_domain="b.example.com",
                    left_agency="mofcom", right_agency="mofcom")
    assert verdict["accepted"] is False
    assert verdict["stage"] == "independence"


def test_rejects_reprint_with_identical_body() -> None:
    """不同域名、不同署名,但正文同稿 ⇒ 必须按转载拒绝,结构化通路不得洗白。"""
    verdict = _pair(right_text=LEFT_TEXT, right_agency="chinanews")
    assert verdict["accepted"] is False
    assert "转载" in verdict["reason"]
    assert verdict["evidence"]["copy_containment"] >= ei.COPY_CONTAINMENT


def test_rejects_when_only_acronym_and_numbers_overlap() -> None:
    """只共享行业缩写(AIGC)与数字,不算同一件事。"""
    left = "华为公司发布AIGC白皮书，市场规模达到1200亿元，同比增长0.83%。"
    right = "腾讯公司发布AIGC报告，市场规模达到1200亿元，同比增长0.83%。"
    verdict = _pair(left_text=left, right_text=right)
    assert verdict["accepted"] is False
    assert "核心" in verdict["reason"] or "具名" in verdict["reason"]
    assert verdict["evidence"]["shared_core"] == []


def test_rejects_when_numbers_insufficient() -> None:
    left = "中指研究院发布报告。刘水表示，挂牌量1200万套，价格上涨3.5%。"
    right = "中指研究院发布报告。刘水表示，挂牌量1200万套，价格下跌4.2%。"
    verdict = _pair(left_text=left, right_text=right)
    assert verdict["accepted"] is False
    assert verdict["evidence"]["shared_numbers"] == ["1200"]


def test_old_title_path_unchanged() -> None:
    """旧口径原样保留:阈值不动,标题相同仍是 1.0,find_pairs 仍按标题成对。"""
    assert si.MIN_EVENT_SCORE == 0.30
    same_title = "中指研究院：11月百城房价环比回落"
    left = _item("https://a.example.com/1", same_title)
    right = _item("https://b.example.com/2", same_title, text=RIGHT_TEXT)
    assert si.event_score(left, right) == 1.0
    pairs, _rejected = si.find_pairs(
        [{**left, "source": "a"}, {**right, "source": "b"}],
        {left["url"]: _ident("yicai"), right["url"]: _ident("nbd")})
    assert len(pairs) == 1 and pairs[0]["score"] == 1.0


def test_find_pairs_structured_keeps_copy_reprint_out() -> None:
    left = _item("https://a.example.com/1", "中指研究院：11月百城房价环比回落")
    right = _item("https://b.example.com/2", "房价继续调整，机构看年末政策窗口", text=RIGHT_TEXT)
    pair_ids = {left["url"]: _ident("yicai"), right["url"]: _ident("nbd")}
    pairs, _rejected, stats = ei.find_pairs_structured([left, right], pair_ids)
    assert len(pairs) == 1 and stats["accepted"] == 1
    assert "text" not in pairs[0]["left"]  # 落盘条目不带正文,防止配对报告膨胀到数百 MB

    copy = _item("https://c.example.com/3", "中指研究院报告被同稿转载", text=LEFT_TEXT)
    copy_ids = {left["url"]: _ident("yicai"), copy["url"]: _ident("chinanews")}
    pairs, rejected, stats = ei.find_pairs_structured([left, copy], copy_ids)
    assert pairs == []
    assert stats["reprint_rejected"] == 1
    assert any("转载" in r["reason"] for r in rejected)
