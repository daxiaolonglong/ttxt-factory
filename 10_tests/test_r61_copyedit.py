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


# ---------------------------------------------------------------- R63 去重升级 + 列举承诺


def test_cross_paragraph_duplicate_is_kept_once():
    text = "延迟法定退休年龄将分步实施。\n\n第二段讲的是别的方面。\n\n延迟法定退休年龄将分步实施。"
    out, fixes = style.copyedit_draft(text)
    assert out.count("延迟法定退休年龄将分步实施") == 1
    assert any(f["kind"] == "cross_line_duplicate" for f in fixes)
    assert out.count("\n\n") == 2, "空行(分段)必须原样保留"


def test_repeated_parallel_clause_is_collapsed():
    text = "坚持小步调整、小步调整、弹性实施、分类推进。"
    out, fixes = style.copyedit_draft(text)
    assert out == "坚持小步调整、弹性实施、分类推进。"
    assert any(f["kind"] == "clause_repeat" for f in fixes)


def test_cite_marker_backfills_into_the_kept_sentence():
    text = "延迟退休年龄将逐步推进。\n\n延迟退休年龄将逐步推进[3]。"
    out, fixes = style.copyedit_draft(text)
    assert out.count("延迟退休年龄将逐步推进") == 1
    assert "逐步推进[3]。" in out, "去重不能把引用证据一起删掉"
    assert any(f["kind"] == "cross_line_duplicate" for f in fixes)


def test_near_duplicate_sentences_are_not_touched():
    text = "延迟退休年龄将分步实施。\n\n延迟退休年龄将分步实施到位。"
    out, fixes = style.copyedit_draft(text)
    assert out.count("延迟退休年龄将分步实施") == 2
    assert fixes == []


def test_fixes_carry_before_and_after():
    text = "这是第一句。这是第一句。这是第二句。"
    _, fixes = style.copyedit_draft(text)
    dup = next(f for f in fixes if f["kind"] == "duplicate_sentence")
    assert "这是第一句" in dup["before"] and "kept_at" in dup


def test_unlisted_quantity_promise_is_detected():
    text = "养老金迎来两件核心大事\n缴费年限要做调整,计发办法也要跟着变。"
    issues = style.enum_promise_issues(text)
    assert len(issues) == 1 and "两件核心大事" in issues[0]


def test_listed_quantity_promise_is_ignored():
    for text in (
        "养老金迎来两件核心大事\n一是缴费年限要做调整,二是计发办法要跟着变。",
        "养老金迎来两件核心大事\n第一件是缴费年限调整,第二件是计发办法调整。",
        "养老金迎来三件核心大事\n- 缴费年限\n- 计发办法\n- 退休年龄",
    ):
        assert style.enum_promise_issues(text) == [], text


def test_no_quantity_promise_no_clue():
    assert style.enum_promise_issues("延迟退休年龄将分步实施,小步调整、弹性实施。") == []
    assert style.enum_promise_issues("") == []


def test_promise_clue_does_not_change_gate():
    from ttxt.writing import workflow

    base = {"score": 0.85, "issues": [], "critical": [], "raw": ""}
    with_clue = {
        "score": 0.85,
        "issues": ["正文承诺了「两件核心大事」但没有逐条列举"],
        "critical": [],
        "raw": "",
        "promise_issues": ["正文承诺了「两件核心大事」但没有逐条列举"],
    }
    assert workflow._passed(True, base, 0.6) is True
    assert workflow._passed(True, with_clue, 0.6) is True, "列举线索不参与放行口径"
    merged = workflow._merge_findings(with_clue, ["确定性结论"], [])
    assert merged["critical"] == [], "列举线索绝不进 critical"
    assert merged["promise_issues"] == with_clue["promise_issues"]
