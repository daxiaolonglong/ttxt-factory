"""R51:相对日期(「明年2月」类)必须按来源 published_at 折算成绝对日期再比对。

锁四件事:
1. claim 相对、来源绝对 ⇒ 折算后命中(旧字面匹配会假 critical);
2. claim 与来源各写「明年2月」但不同年发表 ⇒ 不得字面放行(旧假 verified,跨日/跨年回归);
3. 来源缺 published_at ⇒ unverifiable:不进 missing、credibility 不得 verified、
   critical 措辞如实标「无法折算」而不是「捏造」;
4. 跨年边界(12 月发表、次年 1 月核验;裸月日按发表年定年)折算正确。
"""
from __future__ import annotations

from datetime import datetime

from ttxt.writing import verifier

NOW = datetime(2026, 11, 20, 9, 0, 0)


def _article(text: str, published_at: str | None = None) -> dict:
    article = {"id": 1, "title": "t", "url": "https://e.com/r51", "text": text}
    if published_at is not None:
        article["published_at"] = published_at
    return article


def test_claim_relative_matches_absolute_date_in_source():
    # 来源写绝对 2027年2月,claim 写明年2月(核验时刻 2026)⇒ 折算后同月,必须命中
    text = "该基地计划于2027年2月开始运营。"
    ev = verifier.verify_claim("该基地计划于明年2月开始运营", _article(text, "2026-11-18T10:00:00+08:00"), now=NOW)
    dates = ev["checks"]["dates"]
    assert dates["missing"] == []
    assert dates.get("unverifiable") is None
    assert ev["credibility"] == "verified"


def test_literal_relative_match_across_different_years_is_not_verified():
    # 跨日/跨年回归:来源 2025 年发表,它的「明年2月」=2026年2月;
    # claim 2026 年底写「明年2月」=2027年2月 —— 字面相等但月份不同,必须判缺失
    text = "该基地计划于明年2月开始运营。"
    ev = verifier.verify_claim("该基地计划于明年2月开始运营", _article(text, "2025-06-01T08:00:00+08:00"), now=NOW)
    dates = ev["checks"]["dates"]
    assert dates["missing"] == ["明年2月"]
    assert ev["credibility"] == "unverified"


def test_relative_same_year_resolves_to_verified():
    # 来源 2026 年发表的「明年2月」=2027年2月,与 claim 目标一致 ⇒ 命中
    text = "该基地计划于明年2月开始运营。"
    ev = verifier.verify_claim("该基地计划于明年2月开始运营", _article(text, "2026-05-02T08:00:00+08:00"), now=NOW)
    assert ev["checks"]["dates"]["missing"] == []
    assert ev["credibility"] == "verified"


def test_missing_published_at_marks_unverifiable_not_missing():
    # 折算不出:不得假核(verified),也不得按捏造(missing)处理
    text = "该基地计划于明年2月开始运营。"
    ev = verifier.verify_claim("该基地计划于明年2月开始运营", _article(text), now=NOW)
    dates = ev["checks"]["dates"]
    assert dates["missing"] == []
    unver = dates["unverifiable"]
    assert unver and unver[0]["anchor"] == "明年2月"
    assert unver[0]["resolved_as"] == "2027年2月"
    assert ev["credibility"] == "unverified"


def test_unverifiable_findings_say_unresolvable_not_fabrication():
    ev = verifier.verify_claim(
        "该基地计划于明年2月开始运营", _article("该基地计划于明年2月开始运营。"), now=NOW
    )
    issues, critical = verifier.deterministic_findings([ev], "基地明年2月运营", "该基地计划于明年2月开始运营。")
    joined = ";".join(critical)
    assert "相对日期无法折算(unverifiable)" in joined
    assert "2027年2月" in joined


def test_cross_year_boundary_bare_month_day_uses_publication_year():
    # 来源 2027-01-05 发表,裸「2月10日」按发表年定年 ⇒ 2027-02-10;
    # claim 2026-12-31 写「明年2月10日」=2027-02-10 ⇒ 命中(跨年边界折算)
    text = "该基地将于2月10日开始运营。"
    article = _article(text, "2027-01-05T08:00:00+08:00")
    ev = verifier.verify_claim(
        "该基地将于明年2月10日开始运营", article, now=datetime(2026, 12, 31, 23, 0, 0)
    )
    assert ev["checks"]["dates"]["missing"] == []
    assert ev["credibility"] == "verified"


def test_bare_month_day_without_publication_year_is_unverifiable():
    # 裸月日 + 无 published_at ⇒ 年份不明,不命中也不捏造
    text = "该基地将于2月10日开始运营。"
    ev = verifier.verify_claim(
        "该基地将于明年2月10日开始运营", _article(text), now=datetime(2026, 12, 31, 23, 0, 0)
    )
    dates = ev["checks"]["dates"]
    assert dates["missing"] == []
    assert dates["unverifiable"]
    assert ev["credibility"] == "unverified"


def test_last_year_relative_resolves_with_publication_year():
    # 来源 2026-03 写「去年12月」=2025-12;claim 核验于 2026-11 写「去年12月」=2025-12 ⇒ 命中
    text = "该装置已于去年12月下线。"
    ev = verifier.verify_claim("该装置已于去年12月下线", _article(text, "2026-03-04T09:00:00+08:00"), now=NOW)
    assert ev["checks"]["dates"]["missing"] == []
    assert ev["credibility"] == "verified"


def test_absolute_date_anchor_keeps_old_path():
    # 红线自检:绝对日期锚点的旧组分逻辑不受影响
    ev = verifier.verify_claim(
        "大会于2026年11月7日召开", _article("11月7日,2026首届大会召开。", "2026-11-07T12:00:00+08:00"), now=NOW
    )
    assert ev["checks"]["dates"]["missing"] == []
    assert ev["credibility"] == "verified"
