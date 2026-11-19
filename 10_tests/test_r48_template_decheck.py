"""R48 T-10 第一小步:类目预算(从分布算)+ 生成后去模板化检查的行为钉子。

只测行为,不测实现细节:预算文件缺失/损坏必须如实 not_available(不得当通过)、
超预算必须重写恰好一次、重写失败/仍超标必须留标记、passed 口径不因 template_check 改变。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from ttxt.analysis import rag
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem
from ttxt.writing import workflow
from ttxt.writing.style import (
    FORMULAIC_MARKERS,
    _percentile,
    budget_eval,
    decheck_draft,
    load_budget,
    over_budget_paragraphs,
    template_signals,
)


def _budget_file(tmp_path, marker=5.0, parallel=4.0, min_sentences=12, extra: dict | None = None):
    budgets = {
        "demo": {
            "marker_per_100_sent": marker,
            "parallel_per_100_sent": parallel,
            "basis": "category_p75",
            "basis_drafts": 20,
        }
    }
    if extra:
        budgets.update(extra)
    path = tmp_path / "budgets.json"
    path.write_text(
        json.dumps({"min_sentences": min_sentences, "budgets": budgets}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _over_budget_text(n_per_line=5, lines=4) -> str:
    """20 句、塞满套话:每句 20+ 字,marker 命中远超 5/百句。"""
    filler = "数据变化反映行业结构性调整需要结合本地情况判断"
    lines_text = []
    for i in range(lines):
        sentences = [f"{filler}{i}-{j}。" for j in range(n_per_line)]
        sentences[0] = "首先," + sentences[0]
        sentences[-1] = "与此同时," + sentences[-1]
        lines_text.append("".join(sentences))
    return "\n".join(lines_text)


def _clean_text() -> str:
    filler = "数据变化反映行业结构性调整需要结合本地情况判断"
    lines_text = []
    for i in range(4):
        lines_text.append("".join(f"{filler}{i}-{j}。" for j in range(5)))
    return "\n".join(lines_text)


def test_percentile_linear_interpolation_matches_convention():
    assert _percentile([0.0, 1.0], 0.75) == pytest.approx(0.75)
    assert _percentile([1.0, 2.0, 3.0], 0.5) == pytest.approx(2.0)
    assert _percentile([5.0], 0.75) == pytest.approx(5.0)
    with pytest.raises(ValueError):
        _percentile([], 0.5)


def test_frozen_budget_values_match_documented_rule(tmp_path):
    """生产预算文件必须存在、规则字段齐全,且数值能用同一实现从 probe 快照复算。"""
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "template_budget_compute", root / "scripts" / "template_budget_compute.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    budget_path = root / "00_control" / "template_budgets.json"
    assert budget_path.exists(), "00_control/template_budgets.json 必须随仓库冻结"
    doc = json.loads(budget_path.read_text(encoding="utf-8"))
    probe_path = root / doc["probe_source"]
    recomputed = mod.compute(json.loads(probe_path.read_text(encoding="utf-8")))
    assert recomputed["budgets"] == doc["budgets"], "预算必须可从 probe 快照按 p75 规则复算"
    for cat, entry in doc["budgets"].items():
        assert entry["basis"] in ("category_p75", "pooled_p75_fallback")
        assert entry["basis"] == (
            "category_p75" if entry["basis_drafts"] >= doc["min_basis"] else "pooled_p75_fallback"
        )
        assert entry["marker_per_100_sent"] > 0 and entry["parallel_per_100_sent"] >= 0


def test_load_budget_fail_open_not_silently_passing(tmp_path):
    missing = load_budget("demo", budget_path=tmp_path / "nope.json")
    assert missing["status"] == "not_available"

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_budget("demo", budget_path=broken)["status"] == "not_available"

    assert load_budget("ghost", budget_path=_budget_file(tmp_path))["status"] == "not_available"
    ok = load_budget("demo", budget_path=_budget_file(tmp_path))
    assert ok["status"] == "ok" and ok["marker_per_100_sent"] == 5.0


def test_budget_eval_status_semantics():
    budget = {"status": "ok", "marker_per_100_sent": 5.0, "parallel_per_100_sent": 4.0, "min_sentences": 12}
    sig = template_signals(_over_budget_text())
    assert sig["sentences"] >= budget["min_sentences"]
    assert budget_eval(sig, budget)["status"] == "over_budget"
    assert budget_eval(template_signals(_clean_text()), budget)["status"] == "within_budget"
    short = budget_eval(template_signals("短句。短句。"), budget)
    assert short["status"] == "insufficient_sentences"
    # 预算不可用绝不伪装成通过
    assert budget_eval(sig, {"status": "not_available", "reason": "缺文件"})["status"] == "not_available"


def test_over_budget_paragraphs_localizes_only_hot_paragraphs():
    budget = {"status": "ok", "marker_per_100_sent": 5.0, "parallel_per_100_sent": 4.0, "min_sentences": 12}
    text = _clean_text() + "\n" + _over_budget_text(n_per_line=5, lines=2)
    flagged = over_budget_paragraphs(text, budget)
    assert [f["index"] for f in flagged] == [4, 5]
    assert over_budget_paragraphs(text, {"status": "not_available"}) == []


def test_decheck_within_budget_zero_model_calls(tmp_path):
    calls = []

    def model_call(prompt, **kw):
        calls.append(prompt)
        return "x"

    out = decheck_draft(_clean_text(), "demo", model_call, budget_path=_budget_file(tmp_path))
    assert out["check_status"] == "within_budget"
    assert out["rewrite_count"] == 0 and calls == []
    assert out["draft"] == _clean_text()


def test_decheck_rewrites_once_and_passes(tmp_path):
    seen = []

    def model_call(prompt, **kw):
        seen.append(prompt)
        return _clean_text()

    out = decheck_draft(_over_budget_text(), "demo", model_call, budget_path=_budget_file(tmp_path))
    assert out["check_status"] == "through"
    assert out["rewrite_count"] == 1 and len(seen) == 1
    assert "定向重写" in seen[0]
    assert "首先" in seen[0]  # 原稿必须随 prompt 交给模型
    assert out["draft"] == _clean_text()
    assert out["before"]["status"] == "over_budget"
    assert out["after"]["status"] == "within_budget"


def test_decheck_still_over_budget_marks_not_silently(tmp_path):
    def model_call(prompt, **kw):
        return _over_budget_text()  # 模型没治好

    out = decheck_draft(_over_budget_text(), "demo", model_call, budget_path=_budget_file(tmp_path))
    assert out["check_status"] == "still_over_budget"
    assert out["rewrite_count"] == 1
    assert out["after"]["status"] == "over_budget"
    assert out["after_signals"]["marker_hits"] > 0


def test_decheck_model_failure_keeps_original_and_marks(tmp_path):
    def boom(prompt, **kw):
        raise RuntimeError("model down")

    out = decheck_draft(_over_budget_text(), "demo", boom, budget_path=_budget_file(tmp_path))
    assert out["check_status"] == "rewrite_failed"
    assert out["draft"] == _over_budget_text()
    assert "model down" in out["error"]


def test_decheck_truncated_rewrite_is_rejected(tmp_path):
    def short(prompt, **kw):
        return _over_budget_text()[:50]

    out = decheck_draft(_over_budget_text(), "demo", short, budget_path=_budget_file(tmp_path))
    assert out["check_status"] == "rewrite_failed"
    assert out["draft"] == _over_budget_text()
    assert "60%" in out["error"]


def test_decheck_budget_unavailable_is_honest(tmp_path):
    out = decheck_draft(_over_budget_text(), "demo", lambda p, **kw: "x", budget_path=tmp_path / "nope.json")
    assert out["budget_status"] == "not_available"
    assert out["check_status"] == "not_available"
    assert out["rewrite_count"] == 0


# ------------------------------------------------------------------ 链路级行为(全假模型)

def _item(title: str, content: str, url: str) -> ExtractedItem:
    return ExtractedItem(
        title=title, content=content, excerpt="", author="", published_at=None, url=url
    )


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


def _marker_rich_draft() -> str:
    # 前半段是可核句(与素材原文一致),后半段塞套话 → 整篇与段级都超预算
    core = "人工智能正在改变世界,开源模型让研究更高效[1]。"

    def filler(i: int, j: int) -> str:
        return f"行业结构性变化{i}-{j}需要结合本地数据判断,社区生态也在持续扩展调整口径[1]。"

    paras = []
    for i in range(4):
        sents = [filler(i, j) for j in range(5)]
        sents[0] = "首先," + sents[0]
        sents[-1] = "综上所述," + sents[-1]
        paras.append(core + "".join(sents))
    return "人工智能与开源\n" + "\n".join(paras)


def _clean_rewrite() -> str:
    core = "人工智能正在改变世界,开源模型让研究更高效[1]。"

    def filler(i: int, j: int) -> str:
        return f"行业结构性变化{i}-{j}需要结合本地数据判断,社区生态也在持续扩展调整口径[1]。"

    paras = []
    for i in range(4):
        paras.append(core + "".join(filler(i, j) for j in range(4)))
    return "人工智能与开源\n" + "\n".join(paras)


def _fake_model(state: dict, clean_rewrite: str | None = None):
    def call(prompt, max_tokens=800, temperature=0.3, enable_thinking=False):
        state.setdefault("prompts", []).append(prompt)
        if "请为主题准备写作大纲" in prompt:
            return "- 背景\n- 核心观点\n- 结语"
        if "关键事实陈述" in prompt:
            return '[{"claim": "人工智能正在改变世界,开源模型让研究更高效[1]。", "article_id": 1}]'
        if "严格的中文内容评审" in prompt:
            return '{"score": 0.8, "issues": [], "critical": []}'
        if "定向重写" in prompt:
            state["rewrites"] = state.get("rewrites", 0) + 1
            return clean_rewrite if clean_rewrite is not None else prompt.split("原文:\n", 1)[1]
        return _marker_rich_draft()

    return call


def _write(tmp_path, fake, **kw):
    defaults = dict(
        max_revisions=0,
        db_path=_prepared_db(tmp_path),
        export_dir=tmp_path / "drafts",
        independent_review=False,
        material_min_full=0,
        material_min_sources=1,
        gate_min_chars=0,
    )
    defaults.update(kw)
    return workflow.write_article("demo", "人工智能与开源", model_call=fake, **defaults)


def test_write_workflow_decheck_rewrites_and_freezes_clean_version(tmp_path):
    budget = _budget_file(tmp_path)
    fake = _fake_model({}, clean_rewrite=_clean_rewrite())
    result = _write(tmp_path, fake, budget_path=budget)
    assert result["passed"] is True
    assert result["template_check"]["check_status"] == "through"
    assert result["template_flags"] == []
    assert result["versions"] == 2, "重写必须另存新版本"
    assert result["revisions"] == 0, "去模板化重写不得计入门禁修订计数"
    text = Path(result["path"]).read_text(encoding="utf-8")
    body = text.split("## 正文")[1].split("## 事实证据链")[0]
    for marker in FORMULAIC_MARKERS:
        assert marker not in body


def test_write_workflow_still_over_budget_is_marked_everywhere(tmp_path):
    budget = _budget_file(tmp_path)
    fake = _fake_model({}, clean_rewrite=_marker_rich_draft())  # 重写没治好
    result = _write(tmp_path, fake, budget_path=budget)
    assert result["passed"] is True, "去模板化不得参与 passed 判定"
    assert result["template_check"]["check_status"] == "still_over_budget"
    assert result["template_flags"], "仍超标必须在回执留标记"
    assert any("超预算" in f for f in result["template_flags"])
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "去模板化标记" in text, "导出文件必须带标记清单,不得静默放过"


def test_write_workflow_disabled_skips_everything(tmp_path):
    fake = _fake_model({})
    result = _write(tmp_path, fake, budget_path=_budget_file(tmp_path), template_decheck=False)
    assert result["template_check"] is None
    assert result["versions"] == 1


def test_write_workflow_budget_unavailable_records_not_available(tmp_path):
    fake = _fake_model({})
    result = _write(tmp_path, fake, budget_path=tmp_path / "missing_budgets.json")
    assert result["passed"] is True, "预算缺失不得拦截既有放行链路"
    assert result["template_check"]["budget_status"] == "not_available"
    assert result["template_check"]["check_status"] == "not_available"
    assert result["versions"] == 1, "预算不可用时不得调用重写"
    assert result["template_flags"] and "预算不可用" in result["template_flags"][0], "没尺子也必须露一行"
