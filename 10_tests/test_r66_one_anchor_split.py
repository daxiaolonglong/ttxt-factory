# -*- coding: utf-8 -*-
"""R66:一句一锚点 —— 修复 R63 唯一 critical 的根因链。

R63 烟测稿里的「有人50岁退、有人55岁退，受地方政策…」在来源 18622 上有逐字支撑，
但整句与来源句的词面重叠只有 0.30 < QUOTE_MIN_OVERLAP=0.34，被判 quote_unbound；
后续修订按「删数字留动作」改成「有人退、有人退」，被独立评审判成内容截断。
本组用例钉住确定性修法：只做标点级拆分，让每个数字锚点独占一句，从而可逐句绑定；
不改任何门禁阈值，不增删事实字词。
"""

from ttxt.writing import prompts, style, verifier

SOURCE = {
    "id": 18622,
    "title": "同样是灵活就业女性，为啥有人50岁退 有人55岁退？",
    "url": "https://example.com/18622",
    "text": (
        "经常刷到不少女性朋友在网上吐槽，大家都是自己掏钱交职工养老保险，"
        "身份都是灵活就业，有人50岁就顺利办退休领养老金，"
        "有的人却被告知要等到55岁才能办。"
    ),
}

TARGET = "实际上，有人50岁退、有人55岁退，受地方政策、历史参保身份等因素影响[3]。"


def test_two_number_sentence_is_split_into_single_anchor_sentences():
    out, fixes = style.copyedit_draft("标题：两件事\n\n" + TARGET)
    assert "有人退、有人退" not in out
    assert "实际上，有人50岁退[3]。" in out
    assert "有人55岁退[3]。" in out
    assert any(f["kind"] == "multi_anchor_split" for f in fixes)


def test_split_fact_sentences_bind_to_the_same_source():
    out, _ = style.copyedit_draft("标题\n\n" + TARGET)
    for sentence in ("实际上，有人50岁退[3]。", "有人55岁退[3]。"):
        assert sentence in out
        got = verifier.verify_claim(sentence, SOURCE)
        assert got["credibility"] == "verified", (sentence, got["checks"])
        assert got["quote"], "拆分后每个事实句都必须能逐字绑定来源"


def test_title_is_never_split():
    text = "2026年50岁与55岁退休对照\n\n正文一句话。"
    out, fixes = style.copyedit_draft(text)
    assert out.splitlines()[0] == "2026年50岁与55岁退休对照"
    assert fixes == []


def test_date_or_many_anchor_sentence_is_left_alone():
    # 日期本身就是一个锚点;多锚点政策条款拆开会变碎片,交给 claim 抽取/定点修复。
    for text in (
        "延迟退休政策已于2025年1月1日正式启动[1]。",
        "男、原55岁女干部每4个月延1个月；原50岁女职工每2个月延1个月，逐步过渡到男63岁、女工人55岁、女干部58岁。",
    ):
        out, fixes = style.copyedit_draft("标题\n\n" + text)
        assert text in out
        assert not any(f["kind"] == "multi_anchor_split" for f in fixes)


def test_revise_prompt_bans_dangling_fragments_and_keeps_one_anchor_rule():
    text = prompts.revise_prompt("原文", ["问题"])
    assert "有人退、有人退" in text
    assert "禁止" in text and "残缺" in text
    assert "一句话只写一个数字锚点" in text
    # R63 既有条文不得被替换掉
    assert "删掉该数字、保留动作与条件" in text
    assert "不得换一个数字顶上" in text


def test_repair_prompt_bans_dangling_fragments():
    text = prompts.repair_sentence_prompt("全文", "问题句", "失败", "缺失", "来源", [])
    assert "有人退、有人退" in text
    assert "禁止输出碎片" in text
