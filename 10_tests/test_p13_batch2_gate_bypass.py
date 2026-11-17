"""P13 批次2(账本 T-03):关闭事实核验门禁的两条可绕过根因。

根因①(观点路由过宽):裸 hedge 词(建议/预计/或将/可能…)把整句豁免出事实门禁,
  于是「日期造假」「伪造因果」直接放行 —— P13 批次1 报告 §7 的实例:
  v2h01(A 级正样本)被「建议」判成 opinion 完全跳过门禁;v2t14 的「预计3126年11月15日」
  日期造假同样被豁免。修复后豁免只按「明确归属 + 谓语」判定。

根因②(数字/日期不锚定绑定引句):只要求锚点出现在来源**全文**,等于允许把正文别处
  出现过的数字挪到别的断言上冒充真(移花接木)。修复后数字/日期锚定在
  「绑定引句 ±1 句」的连续窗口内,窗口外命中单独留痕并在 critical 里写明。

这些测试刻意同时断言「修复后的严格结果」和「留痕字段存在」,这样两条根因一旦被改回去就会红。
"""

import pytest

from ttxt.writing import verifier

# --------------------------------------------------------------------------- 观点/事实路由夹具

HEDGE_SRC_TEXT = (
    "2026年3月,星海科技公司发布新一代开源模型。"
    "王芳表示,该模型训练成本为 25 亿元,推理速度提升 3.5%。"
    "世界卫生组织召集的专家小组建议将两款新候选疫苗纳入试点计划。"
    "由于采用了稀疏注意力架构,长文本处理成本大幅下降。"
)
HEDGE_ARTICLE = {"id": 11, "title": "星海科技发布新模型", "url": "https://example.com/h", "text": HEDGE_SRC_TEXT}

# 真转述夹具(T-15 批次4):来源里**确实**有一句第三方归属,紧贴在 25 亿元那句旁边,
# 保证它落在绑定引句 ±1 句的窗口内。少了这句,「业内人士预计」就是伪造归属。
ATTR_SRC_TEXT = (
    "2026年3月,星海科技公司发布新一代开源模型。"
    "王芳表示,该模型训练成本为 25 亿元,推理速度提升 3.5%。"
    "业内人士预计,这一成本数字还会继续发酵。"
    "世界卫生组织召集的专家小组建议将两款新候选疫苗纳入试点计划。"
)
ATTR_ARTICLE = {"id": 13, "title": "星海科技发布新模型", "url": "https://example.com/a", "text": ATTR_SRC_TEXT}

# --------------------------------------------------------------------------- 移花接木夹具

DISPLACE_SRC_TEXT = (
    "2026年3月,星海科技公司发布新一代开源模型,首批装机量达到 88 万台。"
    "王芳表示,该模型推理速度提升 3.5%,长文本处理成本大幅下降。"
    "第二代产品已完成内部测试。"
    "公司内部也重组了两个算法部门。"
    "另据年报,公司计提了 25 亿元的商誉减值,与本次发布无关;2031年才可能并表。"
)
DISPLACE_ARTICLE = {
    "id": 12,
    "title": "星海科技发布新模型",
    "url": "https://example.com/d",
    "text": DISPLACE_SRC_TEXT,
}


def _facts(evidence):
    return [item for item in evidence if item.get("kind") == "fact"]


# --------------------------------------------------------------- 根因①:观点路由按「归属+谓语」

def test_institution_stance_with_jianyi_goes_through_fact_gate():
    """「专家小组建议纳入 X」是机构立场陈述,必须走门禁,不得被「建议」豁免成观点。"""
    claim = "世界卫生组织召集的专家小组建议将两款新候选疫苗纳入试点计划"
    ev = verifier.verify_claim(claim, HEDGE_ARTICLE)
    assert ev["kind"] == "fact"
    assert ev["credibility"] == "verified", ev
    assert ev["checks"]["opinion_markers"] == ["建议"] or "建议" in ev["checks"]["opinion_markers"]


def test_fabricated_date_with_bare_hedge_is_unverified():
    """「预计 + 捏造日期」不再整句豁免:必须进事实门禁并因日期缺失被拒。"""
    claim = "专家小组建议2035年7月8日启动新一轮试点"
    ev = verifier.verify_claim(claim, HEDGE_ARTICLE)
    assert ev["kind"] == "fact"
    assert ev["credibility"] == "unverified"
    assert ev["checks"]["dates"]["missing"]
    issues, critical = verifier.deterministic_findings([ev], "星海科技发布新模型", HEDGE_SRC_TEXT)
    assert any("事实未通过来源核验" in line and "dates:" in line for line in critical)


def test_named_person_prediction_with_number_is_not_exempted():
    """具名人物 + 谓语 + 捏造数字:不是观点,是可核验断言(旧口径会整句放行)。"""
    claim = "王芳预计,该模型训练成本为 999 亿元"
    ev = verifier.verify_claim(claim, HEDGE_ARTICLE)
    assert ev["kind"] == "fact"
    assert ev["credibility"] == "unverified"
    assert "999 亿元" in ev["checks"]["numbers"]["missing"]
    assert "预计" in ev["checks"]["opinion_markers"]  # 语气留痕,但不豁免


def test_attributed_prediction_still_routes_to_opinion():
    """明确「归属 + 谓语」的观点句仍走观点通道,并留下标记。

    T-15(批次4)之后夹具必须**真的**在来源里写过那个归属:
    原来这个测试拿 HEDGE_SRC_TEXT(全文没有「业内人士」)当来源,等于替
    「凭空造一个出处」的写法背书 —— 路由器现在要求第三方归属在绑定引句 ±1 句内逐字存在,
    所以这里换一份带真实归属的来源,并另加一条反向夹具钉住收紧后的行为。
    """
    claim = "业内人士预计,该模型训练成本为 25 亿元将继续发酵"
    ev = verifier.verify_claim(claim, ATTR_ARTICLE)
    assert ev["kind"] == "opinion"
    assert ev["credibility"] == "opinion"
    assert ev["checks"]["opinion_markers"]
    assert ev["checks"]["attribution"]["found"] == ["业内人士预计"]
    assert ev["checks"]["attribution"]["missing"] == []


def test_attribution_absent_from_the_source_no_longer_routes_to_opinion():
    """同一句话,来源里找不到那个归属 ⇒ 不得再走转述通道(T-15 回归)。"""
    claim = "业内人士预计,该模型训练成本为 25 亿元将继续发酵"
    ev = verifier.verify_claim(claim, HEDGE_ARTICLE)
    assert ev["kind"] == "fact"
    assert ev["credibility"] == "unverified"
    assert ev["checks"]["attribution"]["missing"] == ["业内人士预计"]
    _issues, critical = verifier.deterministic_findings([ev], "星海科技发布新模型", HEDGE_SRC_TEXT)
    assert any("attribution_not_in_source" in line for line in critical), critical


def test_first_person_opinion_still_routes_to_opinion():
    claim = "我认为该模型或将改变行业格局"
    ev = verifier.verify_claim(claim, HEDGE_ARTICLE)
    assert ev["credibility"] == "opinion"
    assert ev["checks"]["opinion_markers"]


def test_is_opinion_does_not_match_generic_verbs():
    """「研究院表示」「专家建议」不得被当成观点(否则又变成豁免后门)。"""
    assert verifier.is_opinion("昆仑智能研究院表示,世卫组织建议纳入两款候选疫苗") == []
    assert verifier.is_opinion("该模型可能改变行业格局") == []
    assert verifier.is_opinion("我个人认为这是趋势") != []
    assert verifier.is_opinion("60个人直接向一个人汇报") == []
    assert verifier.hedge_markers("该模型或将改变行业格局") == ["或将"]


# ----------------------------------------------------------- 根因②:数字/日期锚定引句窗口

def test_displaced_number_is_rejected_and_flagged():
    """正文别处出现的 25 亿元不得替引句窗口背书(旧口径会判 verified)。"""
    claim = "王芳表示,该模型推理速度提升 3.5%,训练成本为 25 亿元"
    ev = verifier.verify_claim(claim, DISPLACE_ARTICLE)
    assert ev["credibility"] == "unverified"
    assert "25 亿元" in ev["checks"]["numbers"]["missing"]
    assert ev["checks"]["numbers_outside_quote"] == ["25 亿元"]
    assert ev["checks"]["anchor_window"]["chars"] > 0
    assert verifier.quote_verbatim(ev["quote"], ev["quote_offset"], DISPLACE_SRC_TEXT)
    _, critical = verifier.deterministic_findings([ev], "星海科技发布新模型", DISPLACE_SRC_TEXT)
    assert any("移花接木" in line for line in critical)


def test_displaced_date_is_rejected_and_flagged():
    claim = "王芳表示,该模型推理速度提升 3.5%,并于2031年并表"
    ev = verifier.verify_claim(claim, DISPLACE_ARTICLE)
    assert ev["credibility"] == "unverified"
    assert ev["checks"]["dates_outside_quote"]


def test_legitimate_cross_sentence_quote_still_verified():
    """跨句直引不误伤:锚点落在「引句 ±1 句」窗口内即通过。"""
    claim = "星海科技公司发布新一代开源模型,首批装机量达到 88 万台"
    ev = verifier.verify_claim(claim, DISPLACE_ARTICLE)
    assert ev["credibility"] == "verified", ev
    assert ev["checks"]["numbers"]["missing"] == []


def test_bare_number_cannot_be_swallowed_by_a_longer_number():
    """黄金集 v2t13 暴露的第三条根因:裸数字按子串包含会被长数字吞掉。

    「开售仅3天」改成「开售仅10天」后,`10` 曾被 `10000台` 里的 "10" 命中,
    伪造数字照样 verified。锚点匹配必须带数字边界。
    """
    assert verifier._anchor_in("10", "销量突破10000台") is False
    assert verifier._anchor_in("10", "开售仅10天") is True
    assert verifier._anchor_in("25 亿元", "公司计提了125 亿元的减值") is False
    assert verifier._anchor_in("25 亿元", "成本为 125 亿元") is False
    assert verifier._anchor_in("125 亿元", "成本为 125 亿元") is True
    assert verifier._anchor_in("3.5", "同比增长13.5%") is False
    assert verifier._anchor_in("3.5", "提升 3.5%") is True
    # 中文数量词与非数字锚点仍走普通包含
    assert verifier._anchor_in("三大", "三大运营商") is True
    # 收紧不能误伤原文连写:「2.4亿」后面紧跟「11-17岁」是来源自己的写法(v2h05 实测)
    assert verifier._anchor_in("2.4亿", "这相当于约有2.4亿11-17岁青少年") is True
    assert verifier._anchor_in("11", "2.4亿11-17岁青少年") is True
    assert verifier._anchor_in("17岁", "2.4亿11-17岁青少年") is True


def test_perturbed_day_count_is_rejected_even_when_digits_appear_in_a_bigger_number():
    """v2t13 实例复现:10 天 ≠ 3 天,且 10 不得被 10000 吞掉判成命中。"""
    card_src = (
        "酷派方块数码发文:开售仅3天!酷派阔直板京东销量突破10000台,"
        "这款新机的名字叫Cool X Wide。实话实说,这个成绩太意外了。"
    )
    article = {"id": 13, "title": "酷派阔直板", "url": "https://example.com/v2t13", "text": card_src}
    claim = "酷派方块数码发文:开售仅10天!酷派阔直板京东销量突破10000台,这款新机的名字叫Cool X Wide。"
    ev = verifier.verify_claim(claim, article)
    assert ev["credibility"] == "unverified", ev
    assert "10" in ev["checks"]["numbers"]["missing"]


def test_pure_chinese_numeral_anchor_is_advisory_not_a_hard_gate():
    """纯中文数量词降为建议项:生产实测里「三名航天员」「单一国家」被当成数字 critical 误杀草稿。

    「二」这个锚点在夹具里只出现在窗口外(「重组了两个算法部门」隔在第三句),
    按窗口硬判定会造假 critical;黄金集 61 张里这类锚点没有一张是决定性的。
    """
    base = "王芳表示,该模型推理速度提升 3.5%,长文本处理成本大幅下降"
    assert verifier.verify_claim(base, DISPLACE_ARTICLE)["credibility"] == "verified"
    ev = verifier.verify_claim(base + "。这是第二次公开发布", DISPLACE_ARTICLE)
    assert ev["quote"]
    assert ev["credibility"] == "verified", ev
    assert ev["checks"]["numbers"]["missing"] == []
    advisory = ev["checks"].get("numbers_advisory") or []
    assert any(item["anchor"] == "二" for item in advisory)
    assert "numbers_outside_quote" not in ev["checks"]


def test_anchor_window_is_contiguous_source_slice():
    quote = "王芳表示,该模型推理速度提升 3.5%,长文本处理成本大幅下降。"
    offset = DISPLACE_SRC_TEXT.index(quote)
    window, window_offset = verifier.anchor_window(DISPLACE_SRC_TEXT, quote, offset)
    assert DISPLACE_SRC_TEXT[window_offset : window_offset + len(window)] == window
    assert quote in window
    assert "25 亿元" not in window  # 隔了两句,必须落在窗口外
    assert "88 万台" in window


def test_missing_quote_still_blocks_every_anchor():
    ev = verifier.verify_claim("台风登陆海南造成航班大面积取消", DISPLACE_ARTICLE)
    assert ev["credibility"] == "unverified"
    assert ev["overlap"] < verifier.QUOTE_MIN_OVERLAP
