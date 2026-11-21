# -*- coding: utf-8 -*-
"""R61 G5:写作 few-shot 样本库(只用实测样本,库空/缺失自动降级)。

三条红线(对应 V3.0「不编样本、只收紧不放宽」):
1. 样本必须 `verified=true` 且 kind 合法,类目匹配,字段齐全 —— 手工编的样本进不来;
2. 库缺失/损坏/为空 ⇒ `load_samples` 返回空 + 如实状态串,写作链路降级为无示例,
   回执 `fewshot_status` 不许把「没样本」写成功;
3. 注入只进草稿提示词(正例 + 反例,每条带 note),门禁与判定尺度(800 字、事实门禁、
   评审阈值)一律不动;回执记 `fewshot_count`。

不依赖真实模型:写作侧用替身 `FakeDraftModel`,其余同 test_p40 的离线姿势。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ttxt.analysis import rag
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem
from ttxt.writing import fewshot, prompts, workflow


def _item(title: str, content: str, url: str) -> ExtractedItem:
    return ExtractedItem(title=title, content=content, excerpt="", author="", published_at=None, url=url)


def _prepared_db(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "a.db")
    store.insert_items(
        "demo",
        "test",
        [_item("人工智能与开源", "人工智能正在改变世界,开源模型让研究更高效。", "https://example.com/1")],
        db_path=db_path,
    )
    store.migrate_articles_db("demo", db_path=db_path)
    rag.rebuild_index("demo", db_path=db_path)
    return db_path


def _sample(category="social", kind="verified_viral", **over):
    item = {
        "category": category,
        "topic": "延迟退休 养老金",
        "title": "延迟退休落地后,养老金这样算",
        "excerpt": "自 2025 年 1 月 1 日起,渐进式延迟退休开始实施[1]。",
        "kind": kind,
        "score": 0.85,
        "fact_checkability": 1.0,
        "note": "标题给结论,正文首句只装一个日期锚点,引用逐字可定位。",
        "verified": True,
    }
    item.update(over)
    return item


def _write_library(tmp_path, samples):
    path = tmp_path / "fewshot_library.json"
    path.write_text(
        json.dumps({"version": 1, "updated_at": "2026-11-21", "samples": samples}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- 单元:降级与过滤


def test_missing_library_degrades():
    samples, status = fewshot.load_samples("social", library_path=Path("X:/no/such/file.json"))
    assert samples == []
    assert status == "library_missing"


def test_empty_library_degrades(tmp_path):
    path = _write_library(tmp_path, [])
    samples, status = fewshot.load_samples("social", library_path=path)
    assert samples == []
    assert status == "library_empty"


def test_unreadable_library_degrades(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    samples, status = fewshot.load_samples("social", library_path=path)
    assert samples == []
    assert status == "library_unreadable"


def test_unverified_wrong_category_wrong_kind_incomplete_are_filtered(tmp_path):
    path = _write_library(
        tmp_path,
        [
            _sample(category="social"),
            _sample(category="finance"),                       # 类目不符
            _sample(verified=False),                           # 未实测
            _sample(kind="handwritten"),                       # kind 非法
            _sample(excerpt=""),                               # 字段不齐
            _sample(note=""),
            _sample(category="social"),                        # 第二条 social 正例
        ],
    )
    samples, status = fewshot.load_samples("social", library_path=path)
    assert [s["category"] for s in samples] == ["social", "social"]
    assert status == "ok:2"


def test_limit_is_respected(tmp_path):
    path = _write_library(tmp_path, [_sample() for _ in range(8)])
    samples, status = fewshot.load_samples("social", limit=3, library_path=path)
    assert len(samples) == 3
    assert status == "ok:3"


def test_no_verified_samples_status(tmp_path):
    path = _write_library(tmp_path, [_sample(category="finance")])
    samples, status = fewshot.load_samples("social", library_path=path)
    assert samples == []
    assert status == "no_verified_samples"


# --------------------------------------------------------------------------- 提示词拼装


def test_format_labels_positive_and_negative():
    samples = [_sample(kind="verified_viral"), _sample(kind="verified_junk", note="把三个日期挤进一句")]
    text = fewshot.format_samples(samples)
    assert "【正例1】" in text and "可学之处:" in text
    assert "【反例2(禁止模仿)】" in text and "问题:" in text
    assert samples[0]["title"] in text and samples[1]["excerpt"] in text


def test_format_empty_is_empty():
    assert fewshot.format_samples([]) == ""


def test_draft_prompt_is_unchanged_without_fewshot():
    text = prompts.draft_prompt("主题", "- 一", "材料")
    assert "写作示例" not in text
    assert "参考材料:\n材料\n要求:" in text


def test_draft_prompt_injects_fewshot_block_before_requirements():
    block = fewshot.format_samples([_sample()])
    text = prompts.draft_prompt("主题", "- 一", "材料", fewshot_text=block)
    assert block in text
    assert text.index(block) < text.index("要求:")


# --------------------------------------------------------------------------- 集成:注入与回执


class FakeDraftModel:
    """只走草稿提示词的替身:大纲/claims/评审按离线脚本,草稿记录收到的提示词。"""

    def __init__(self):
        self.draft_prompts = []
        self.calls = {"outline": 0, "draft": 0, "claims": 0, "review": 0}

    def __call__(self, prompt, max_tokens=800, temperature=0.3, enable_thinking=False):
        if "请为主题准备写作大纲" in prompt:
            self.calls["outline"] += 1
            return "- 背景\n- 核心观点\n- 结语"
        if "关键事实陈述" in prompt:
            self.calls["claims"] += 1
            return '[{"claim": "人工智能正在改变世界", "article_id": 1}]'
        if "严格的中文内容评审" in prompt:
            self.calls["review"] += 1
            return '{"score": 0.8, "issues": [], "critical": []}'
        if "根据评审意见修改文章" in prompt:
            return "人工智能与开源\n人工智能正在改变世界。"
        self.calls["draft"] += 1
        self.draft_prompts.append(prompt)
        return "人工智能与开源\n人工智能正在改变世界,开源模型让研究更高效。"


@pytest.fixture(autouse=True)
def _isolated_library(monkeypatch):
    """默认库路径指到不存在的位置:库空/缺失的真实降级路径被单元测试覆盖,
    集成测试的降级分支不许依赖 00_control 的真实文件内容。"""
    monkeypatch.setattr(fewshot, "LIBRARY_PATH", Path("X:/no/fewshot_library.json"))


def test_write_article_degrades_and_receipt_is_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        workflow,
        "_model_meta",
        lambda model_call: {
            "same_model_review": True,
            "reviewer_model": None,
            "reviewer_limitation": "测试替身",
            "served_model": "writer-model-under-test",
        },
    )
    db_path = _prepared_db(tmp_path)
    fake = FakeDraftModel()
    result = workflow.write_article(
        "demo",
        "人工智能与开源",
        model_call=fake,
        max_revisions=0,
        db_path=db_path,
        export_dir=tmp_path / "drafts",
        material_min_full=0,
        material_min_sources=1,
        gate_min_chars=0,
        gate_min_score=0.6,
    )
    assert result["fewshot_count"] == 0
    assert result["fewshot_status"] == "library_missing"
    assert all("写作示例" not in p for p in fake.draft_prompts), "库缺失时提示词不得出现示例块"


def test_write_article_injects_samples_into_draft_prompt_only(tmp_path, monkeypatch):
    monkeypatch.setattr(
        workflow,
        "_model_meta",
        lambda model_call: {
            "same_model_review": True,
            "reviewer_model": None,
            "reviewer_limitation": "测试替身",
            "served_model": "writer-model-under-test",
        },
    )
    samples = [_sample(kind="verified_viral"), _sample(kind="verified_junk", note="三个日期挤一句")]
    monkeypatch.setattr(workflow.fewshot, "load_samples", lambda category, **kw: (samples, "ok:2"))
    db_path = _prepared_db(tmp_path)
    fake = FakeDraftModel()
    result = workflow.write_article(
        "demo",
        "人工智能与开源",
        model_call=fake,
        max_revisions=0,
        db_path=db_path,
        export_dir=tmp_path / "drafts",
        material_min_full=0,
        material_min_sources=1,
        gate_min_chars=0,
        gate_min_score=0.6,
    )
    assert result["fewshot_count"] == 2
    assert result["fewshot_status"] == "ok:2"
    assert fake.calls["draft"] >= 1
    assert all("【正例1】" in p and "【反例2(禁止模仿)】" in p for p in fake.draft_prompts)
    # 大纲与 claims 提示词不受注入影响(它们不接收 fewshot 参数)
    assert fake.calls["outline"] >= 1
    assert fake.calls["claims"] >= 1
    # 门禁尺度不动:本次用 0 分阈值放行,但事实门禁与评审口径仍是原链路在跑
    assert result["passed"] is True
