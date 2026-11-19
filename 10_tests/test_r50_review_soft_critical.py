"""R50 回归:评审"让步+转折"软意见不得被 CRITICAL_HINTS 子串判死。

锁死 R49 探针生产实测被误杀的两条原话(technology 人工智能 r1 / finance 财报 r2):
- "第二段关于美国芯片政策导致中国企业去美化的论述逻辑略显简化，虽引用来源但缺乏具体数据支撑"
- "第二段引用报告时未明确提及具体发布时间或背景，虽不影响事实准确性但略减信息密度"
两条都必须降级为 issues(无论走 CRITICAL_HINTS 升级还是模型自报 critical 数组),
critical 保持为空;而真的负面断言("无来源断言"等)必须照常判死。
"""

from __future__ import annotations

import json

from ttxt.writing.workflow import _soft_concession, parse_review

SOFT_SOURCE = "第二段关于美国芯片政策导致中国企业去美化的论述逻辑略显简化，虽引用来源但缺乏具体数据支撑"
SOFT_FACT = "第二段引用报告时未明确提及具体发布时间或背景，虽不影响事实准确性但略减信息密度"


def _review(score: float, issues: list[str], critical: list[str]) -> str:
    return json.dumps({"score": score, "issues": issues, "critical": critical}, ensure_ascii=False)


def test_soft_concession_predicates():
    assert _soft_concession(SOFT_SOURCE) is True
    assert _soft_concession(SOFT_FACT) is True


def test_promotion_path_does_not_kill_soft_opinions():
    # critical 数组为空 → 走 CRITICAL_HINTS 升级路径;两句原话都不得升级成 critical
    out = parse_review(_review(0.95, [SOFT_SOURCE, SOFT_FACT], []))
    assert out["critical"] == []
    assert SOFT_SOURCE in out["issues"]
    assert SOFT_FACT in out["issues"]


def test_model_self_reported_critical_is_demoted():
    # 模型把软意见塞进 critical 数组 → 同样降级回 issues,不得判死
    out = parse_review(_review(0.95, [], [SOFT_FACT]))
    assert out["critical"] == []
    assert out["issues"] == [SOFT_FACT]


def test_genuine_critical_still_kills_via_promotion():
    out = parse_review(_review(0.4, ["第三段出现无来源断言", "有个别错别字"], []))
    assert out["critical"] == ["第三段出现无来源断言"]


def test_genuine_critical_after_contrast_survives():
    # critical 词落在转折后半句(真的负面断言)→ 照常判死
    genuine = "这段数据与来源矛盾，虽然篇幅简短但属于事实错误"
    out = parse_review(_review(0.4, [genuine], []))
    assert out["critical"] == [genuine]
    out2 = parse_review(_review(0.4, [], [genuine]))
    assert out2["critical"] == [genuine]
