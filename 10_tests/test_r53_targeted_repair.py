"""R53 定点修复的行为钉子:只重写失败句、复验用门禁同一套判定、修不好如实记失败形状。

钉子口径:
- 一次修好 / 两轮修好 / 修不好(两轮上限)三条路径的计数必须准确;
- 只有复验(verify_claim)通过的句子才替换进草稿与证据行;修不好的句子与证据行原样不动;
- 修不好的失败形状如实记录(脑补/绑错文章/移花接木/未绑定/无来源/伪造归属);
- 门禁统计不因修复失真:失败 claim 不被剔出证据,确定性 critical 照常产生;
- 定位失败(词面重叠不足)宁可放弃修复也不在错误句子上动刀。
"""

from __future__ import annotations

import pytest

from ttxt.writing import repair, verifier, workflow

MATERIALS = [
    {
        "id": 1,
        "title": "来源甲",
        "url": "http://a.example/1",
        "text": "财报显示,该业务营收为23亿元。公司称前景乐观。",
        "published_at": "2025-11-12T00:00:00",
    },
    {
        "id": 2,
        "title": "来源乙",
        "url": "http://b.example/2",
        "text": "另外,该业务利润为32亿元。",
        "published_at": "2025-11-12T00:00:00",
    },
]


def _evidence(claims: list[dict]) -> list[dict]:
    return workflow.verify_claims(claims, MATERIALS)


class _StubCall:
    """按脚本依次返回改写输出;记录每次收到的 prompt 供断言。"""

    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    def __call__(self, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        if not self.outputs:
            raise AssertionError("model_call 被调用次数超出脚本")
        return self.outputs.pop(0)


def test_one_round_fix_replaces_only_the_failed_sentence():
    draft = "标题行\n财报显示,该业务营收为99亿元。公司称前景乐观。"
    evidence = _evidence([{"claim": "财报显示,该业务营收为99亿元", "article_id": 1, "quote": None}])
    assert evidence[0]["credibility"] == "unverified"  # 99亿 来源里没有 ⇒ 先确认确实失败

    stub = _StubCall(["财报显示,该业务营收为23亿元"])
    new_draft, new_evidence, stats = repair.repair_failing_claims(stub, draft, evidence, MATERIALS)

    assert stats["fixed_first"] == 1 and stats["unfixed"] == 0
    assert stats["model_calls"] == 1 and stats["attempts"] == 1
    assert new_evidence[0]["credibility"] == "verified"
    assert "99亿元" not in new_draft and "23亿元" in new_draft
    # 上下文不动:只换那一句
    assert new_draft == "标题行\n财报显示,该业务营收为23亿元。公司称前景乐观。"
    # 请求里带上它原本应绑的来源引句窗口
    assert "该业务营收为23亿元" in stub.prompts[0]
    assert "99亿元" in stub.prompts[0]  # 失败详情照实进请求


def test_two_round_cap_unfixable_stays_failed_with_shape_recorded():
    draft = "标题行\n财报显示,该业务营收为99亿元。公司称前景乐观。"
    evidence = _evidence([{"claim": "财报显示,该业务营收为99亿元", "article_id": 1, "quote": None}])

    stub = _StubCall(["财报显示,该业务营收为120亿元", "财报显示,该业务营收为120亿元"])
    new_draft, new_evidence, stats = repair.repair_failing_claims(stub, draft, evidence, MATERIALS)

    assert stats["unfixed"] == 1 and stats["fixed_first"] == 0 and stats["fixed_second"] == 0
    assert stats["attempts"] == 2 and stats["model_calls"] == 2  # 每条 claim 最多 2 轮
    # 修不好 ⇒ 句子与证据行原样保留,不得被"修成通过"
    assert new_draft == draft
    assert new_evidence[0]["credibility"] == "unverified"
    # 失败形状如实记录:锚点全池找不到的脑补
    shapes = stats["unfixed_shapes"]
    assert list(shapes) and next(iter(shapes)).startswith("fabricated")
    # 门禁统计不因修复失真:失败 claim 照进确定性 critical
    issues, critical = verifier.deterministic_findings(new_evidence, "标题行", new_draft)
    assert any("事实未通过来源核验" in c for c in critical)


def test_invalid_output_same_as_original_still_judged_by_reverification():
    draft = "标题行\n财报显示,该业务营收为99亿元。"
    evidence = _evidence([{"claim": "财报显示,该业务营收为99亿元", "article_id": 1, "quote": None}])
    stub = _StubCall(["财报显示,该业务营收为99亿元", "财报显示,该业务营收为99亿元"])
    new_draft, new_evidence, stats = repair.repair_failing_claims(stub, draft, evidence, MATERIALS)
    # 原句回声在全池复验里照样过不了 ⇒ 不得被"修成通过"
    assert stats["attempts"] == 2 and stats["unfixed"] == 1 and stats["fixed_first"] == 0
    assert new_draft == draft and new_evidence[0]["credibility"] == "unverified"


def test_anchor_erased_rewrite_cannot_pass_reverification():
    """带锚点的失败句被改写成无锚点空话:不得借"复验通过"洗白(修复层比门禁更严)。"""
    draft = "标题行\n财报显示,该业务营收为99亿元。"
    evidence = _evidence([{"claim": "财报显示,该业务营收为99亿元", "article_id": 1, "quote": None}])
    stub = _StubCall(["前景总体乐观", "形势总体乐观"])
    new_draft, new_evidence, stats = repair.repair_failing_claims(stub, draft, evidence, MATERIALS)
    assert stats["anchor_erased"] == 2
    assert stats["unfixed"] == 1 and stats["fixed_first"] == 0
    assert new_draft == draft and new_evidence[0]["credibility"] == "unverified"


def test_second_round_success_counted_as_fixed_second():
    draft = "标题行\n财报显示,该业务营收为99亿元。公司称前景乐观。"
    evidence = _evidence([{"claim": "财报显示,该业务营收为99亿元", "article_id": 1, "quote": None}])
    stub = _StubCall(["财报显示,该业务营收为150亿元", "财报显示,该业务营收为23亿元"])
    new_draft, new_evidence, stats = repair.repair_failing_claims(stub, draft, evidence, MATERIALS)
    assert stats["fixed_second"] == 1 and stats["attempts"] == 2
    assert new_evidence[0]["credibility"] == "verified"
    assert "150亿元" not in new_draft


def test_wrong_binding_repaired_by_rebinding_to_the_article_that_has_anchor():
    draft = "标题行\n财报显示,该业务利润为32亿元。"
    evidence = _evidence([{"claim": "财报显示,该业务利润为32亿元", "article_id": 1, "quote": None}])
    # 32亿 在绑定文章(甲)里没有、在池内文章(乙)里有 ⇒ 绑错文章
    assert evidence[0]["credibility"] == "unverified"

    stub = _StubCall(["财报显示,该业务利润为32亿元"])
    new_draft, new_evidence, stats = repair.repair_failing_claims(stub, draft, evidence, MATERIALS)

    # 复验逐一试过候选:换绑到真正承载锚点的乙才允许通过
    assert stats["fixed_first"] == 1 and stats["rebound"] == 1
    assert new_evidence[0]["article_id"] == 2
    assert new_evidence[0]["credibility"] == "verified"
    # 修复请求必须带上锚点所在的那篇原句候选
    assert "该业务利润为32亿元" in stub.prompts[0]


def test_unlocatable_claim_is_recorded_not_guessed():
    draft = "标题行\n财报显示,该业务营收为99亿元。"
    evidence = _evidence([{"claim": "完全不相干的一句别的东西", "article_id": 1, "quote": None}])
    if evidence[0]["credibility"] == "verified":
        pytest.skip("构造未失败")
    stub = _StubCall([])
    new_draft, new_evidence, stats = repair.repair_failing_claims(stub, draft, evidence, MATERIALS)
    assert stats["not_located"] == 1
    assert stats["model_calls"] == 0  # 定位不了就绝不在错误的句子上动刀
    assert new_draft == draft and new_evidence[0] is evidence[0]


def test_locator_fallback_accepts_paraphrase_but_rejects_low_overlap():
    draft = "财报显示,当天公司成交额达到99亿元,市场普遍瞩目。下一句。"
    loc = repair.locate_sentence(draft, "当天公司成交额达到99亿元")
    assert loc is not None  # 词面重叠达标的近逐字 claim 能定位
    assert repair.locate_sentence(draft, "完全不同的话题句子甲乙丙") is None


def test_verified_and_opinion_rows_never_touched():
    draft = "我认为前景很好。财报显示,该业务营收为23亿元。"
    evidence = _evidence(
        [
            {"claim": "我认为前景很好", "article_id": 1, "quote": None},
            {"claim": "财报显示,该业务营收为23亿元", "article_id": 1, "quote": None},
        ]
    )
    assert evidence[0]["credibility"] == "opinion"
    assert evidence[1]["credibility"] == "verified"
    stub = _StubCall([])
    new_draft, new_evidence, stats = repair.repair_failing_claims(stub, draft, evidence, MATERIALS)
    assert stats["facts_failed"] == 0 and stats["attempts"] == 0
    assert new_draft == draft
    assert [r["credibility"] for r in new_evidence] == ["opinion", "verified"]


def test_repair_prompt_prefers_dropping_unsourced_numbers():
    """R63:修复建议句时去掉无来源数字,而不是换一个数字(与 anchor_erased 守卫同向不冲突)。"""
    from ttxt.writing import prompts

    text = prompts.repair_sentence_prompt("全文", "问题句", "失败详情", "numbers", "来源块", ["候选原句"])
    assert "删掉这个数字、只保留动作与条件就是正确修法" in text
    assert "不得换一个数字或区间顶上" in text
    # 空话守卫与一句一锚点必须原样还在(不得为了过闸洗掉锚点)
    for kept in (
        "新句子必须仍带至少一个事实锚点",
        "不得改写成没有事实的空话",
        "一条句子只装一个事实锚点",
        "绝不编造、换算、近似数字/日期",
    ):
        assert kept in text
