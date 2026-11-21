"""R61 0 产出根因:生成侧确定性去重(只删相邻重复,不改写事实、不动门禁)。"""

from ttxt.writing import style


def test_adjacent_repeated_phrase_is_collapsed_once():
    text = "男、原55岁女干部每4个月延1个月原55岁女干部每4个月延1个月原50岁女职工每2个月延1个月。"
    out, fixes = style.copyedit_draft(text)
    assert out.count("原55岁女干部每4个月延1个月") == 1
    assert "原50岁女职工" in out
    assert fixes and fixes[0]["kind"] == "adjacent_repeat"


def test_duplicate_sentence_is_removed():
    text = "这是第一句。这是第一句。这是第二句。"
    out, fixes = style.copyedit_draft(text)
    assert out == "这是第一句。这是第二句。"
    assert any(f["kind"] == "duplicate_sentence" for f in fixes)


def test_clean_text_is_unchanged():
    text = "标题行\n第一段正常内容,没有重复。\n第二段也正常。"
    out, fixes = style.copyedit_draft(text)
    assert out == text
    assert fixes == []


def test_short_repeats_and_title_are_not_touched():
    text = "标题标题标题\n正常叠词如研究研究、哈哈哈不算生成故障。"
    out, fixes = style.copyedit_draft(text)
    assert out.splitlines()[0] == "标题标题标题"
    assert "研究研究" in out
    assert fixes == []


def test_markdown_table_separator_is_not_collapsed():
    text = "| 项 | 值 | 说明 |\n|---|---|---|\n| 一 | 二 | 三 |"
    out, fixes = style.copyedit_draft(text)
    assert out == text
    assert fixes == []


def test_revise_retries_once_when_output_truncated():
    from ttxt.writing import workflow

    calls: list[int] = []

    def fake(prompt, max_tokens=800, temperature=0.3, enable_thinking=False):
        calls.append(max_tokens)
        if len(calls) == 1:
            return "短句"
        return "这是一段足够长的完整修订正文,没有截断。"

    out = workflow._revise_with_retry(
        fake,
        "原文",
        ["问题"],
        max_tokens=100,
        retry_cap=200,
        gate_min_chars=10,
        enable_thinking=False,
        topic="测试",
    )
    assert calls == [100, 200], "第一次截断后应按草稿先例放宽 token 重试一次"
    assert out.startswith("这是一段")
