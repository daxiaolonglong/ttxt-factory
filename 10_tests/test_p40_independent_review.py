"""P40 独立评审通道(账本 T-04/T-05)。

要证明的不是"能跑",而是四条红线:
1. 提示词只有 6 个字「审评这篇文章」+ 正文,不带评分细则,不做成配置项(避免被写作侧口径诱导);
2. 密钥只从环境变量/文件读,绝不进回执、日志、DB、异常文本;
3. **评审模型与写作模型同源 ⇒ 不得算 L4 通过**(`same_model_review` 仍为 True,版本账目不得给
   INDEPENDENT_REVIEW_PASSED),独立性只按两侧真实 id 比较;
4. 独立评审只能收紧门禁:判阻断就拒绝,通道不可用就按"未解除同源"处理,不得当成通过。
"""

import json
import sqlite3

import pytest

from ttxt import config as cfg
from ttxt.analysis import rag
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem
from ttxt.model_gate import client as model_client
from ttxt.writing import review as review_channel
from ttxt.writing import workflow

SENTINEL = "sk-SENTINEL-DO-NOT-LEAK-abcdef"
WRITER_ID = "writer-model-under-test"


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


class FakeModel:
    """写作侧替身:大纲/事实陈述/正文/评审各按脚本返回。"""

    def __init__(self, review_texts=None):
        self.calls = {"outline": 0, "draft": 0, "claims": 0, "review": 0, "revise": 0}
        self.review_texts = review_texts or []

    def _complete(self):
        return "人工智能与开源生态\n人工智能正在改变世界,开源模型让研究更高效,这是一个完整的结束句。"

    def __call__(self, prompt, max_tokens=800, temperature=0.3, enable_thinking=False):
        if "请为主题准备写作大纲" in prompt:
            self.calls["outline"] += 1
            return "- 背景\n- 核心观点\n- 结语"
        if "关键事实陈述" in prompt:
            self.calls["claims"] += 1
            return '[{"claim": "人工智能正在改变世界", "article_id": 1}]'
        if "严格的中文内容评审" in prompt:
            index = self.calls["review"]
            self.calls["review"] += 1
            if index < len(self.review_texts):
                return self.review_texts[index]
            return '{"score": 0.8, "issues": [], "critical": []}'
        if "根据评审意见修改文章" in prompt:
            return self._complete()
        return self._complete()


def _outcome(reviewer_id: str, text: str) -> dict:
    return {
        "reviewer_model": reviewer_id,
        "verdict_text": text,
        "verdict": review_channel.verdict_markers(text),
        "prompt": review_channel.REVIEW_PROMPT,
        "finish_reason": "stop",
        "usage": {"completion_tokens": 10},
        "elapsed_s": 0.1,
    }


def _run(tmp_path, monkeypatch, *, review_call=None, independent_review=None, review_texts=None,
         served_model=WRITER_ID):
    monkeypatch.setattr(
        workflow,
        "_model_meta",
        lambda model_call: {
            "same_model_review": True,
            "reviewer_model": None,
            "reviewer_limitation": "测试替身",
            "served_model": served_model,
        },
    )
    db_path = _prepared_db(tmp_path)
    fake = FakeModel(review_texts=review_texts)
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
        independent_review=independent_review,
        review_call=review_call,
    )
    return db_path, result


def _history(db_path):
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT version, status FROM writing_history ORDER BY version"
        ).fetchall()
        evals = con.execute(
            "SELECT evaluator, score, criteria_json FROM evaluation_history ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    return rows, evals


# --------------------------------------------------------------------------- 提示词与判定口径


def test_prompt_is_the_ruled_six_chars_and_not_configurable():
    assert review_channel.REVIEW_PROMPT == "审评这篇文章"
    assert len(review_channel.REVIEW_PROMPT) == 6, "项目负责人定死 6 个字,多一字少一字都算违规"
    assert "prompt" not in review_channel.review_config(), "提示词不得做成配置项,否则细则能反过来诱导评审"


def test_verdict_marker_matrix():
    assert review_channel.verdict_markers("整体可以发布。")["publishable"] is True
    assert review_channel.verdict_markers("数据不实,不建议发布。")["blocking"] is True
    both = review_channel.verdict_markers("不建议发布,但修好后可以发布。")
    assert both["blocking"] is True and both["publishable"] is False, "阻断措辞优先,不给许可标签"
    neither = review_channel.verdict_markers("还行吧。")
    assert neither["blocking"] is False and neither["publishable"] is False, "没许可措辞就不给独立标签"


# --------------------------------------------------------------------------- 密钥红线


def test_missing_key_raises_without_leaking_anything(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    with pytest.raises(review_channel.ReviewUnavailable) as exc:
        review_channel.api_key({"api_key_env": "DEEPSEEK_API_KEY", "api_key_file": ".secrets/gone"})
    assert "DEEPSEEK_API_KEY" in str(exc.value)


def test_key_reads_env_then_file_without_network(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    secret = tmp_path / ".secrets"
    secret.mkdir()
    (secret / "deepseek_api_key").write_text(SENTINEL + "\n", encoding="utf-8")
    assert review_channel.api_key({"api_key_file": ".secrets/deepseek_api_key"}) == SENTINEL
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert review_channel.api_key({"api_key_file": ".secrets/deepseek_api_key"}) == "from-env"


def test_review_request_carries_only_prompt_and_draft_and_no_secret(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL)
    captured: dict = {}

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return {"model": "deepseek-flash", "text": "论证尚可,可以发布。", "finish_reason": "stop",
                "usage": {"completion_tokens": 7}, "elapsed_s": 0.2}

    monkeypatch.setattr(model_client, "models", lambda **kw: ["deepseek-flash"])
    monkeypatch.setattr(model_client, "chat", fake_chat)
    logs: list = []
    monkeypatch.setattr(review_channel.LOG, "info", lambda event, **kw: logs.append((event, kw)))

    outcome = review_channel.review("正文第一行\n正文第二行")

    sent = captured["messages"][0]["content"]
    assert sent == review_channel.REVIEW_PROMPT + "\n\n正文第一行\n正文第二行"
    assert len(captured["messages"]) == 1, "盲评只发一条消息,不给评分细则"
    for phrase in ("评分", "细则", "score", "致命"):
        assert phrase not in sent
    assert captured["api_key"] == SENTINEL
    assert captured["model_id"] == "deepseek-flash"
    assert captured["base_url"] == review_channel.review_config()["base_url"]
    assert outcome["reviewer_model"] == "deepseek-flash"
    assert outcome["verdict"]["publishable"] is True
    blob = json.dumps(outcome, ensure_ascii=False) + repr(logs)
    assert SENTINEL not in blob, "密钥不得进返回值或日志"
    assert captured["max_tokens"] >= 4000, "推理模型 max_tokens 含推理链,给小了正文会是空的"


def test_unlisted_model_and_dead_endpoint_both_mean_unavailable(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL)
    monkeypatch.setattr(model_client, "models", lambda **kw: ["some-other-model"])
    with pytest.raises(review_channel.ReviewUnavailable) as exc:
        review_channel.served_model()
    assert "deepseek-flash" in str(exc.value)

    def boom(**kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(model_client, "models", boom)
    with pytest.raises(review_channel.ReviewUnavailable) as exc2:
        review_channel.served_model()
    assert SENTINEL not in str(exc2.value)


def test_http_client_threads_the_review_key_not_the_writing_one(monkeypatch):
    monkeypatch.delenv("STRATA_API_KEY", raising=False)
    assert "Authorization" not in model_client._headers(None)
    assert model_client._headers(SENTINEL)["Authorization"] == f"Bearer {SENTINEL}"
    monkeypatch.setenv("STRATA_API_KEY", "writing-side-key")
    assert model_client._headers(None)["Authorization"] == "Bearer writing-side-key"
    assert model_client._headers(SENTINEL)["Authorization"] == f"Bearer {SENTINEL}"


# --------------------------------------------------------------------------- 写作链路:独立性判定


def test_different_reviewer_id_establishes_independence(tmp_path, monkeypatch):
    seen: list = []

    def review_call(draft):
        seen.append(draft)
        return _outcome("deepseek-flash", "结构与事实基本一致,可以发布。")

    db_path, result = _run(tmp_path, monkeypatch, review_call=review_call)
    assert result["model_meta"]["same_model_review"] is False
    assert result["model_meta"]["reviewer_model"] == "deepseek-flash"
    assert result["review_account"] == "INDEPENDENT_REVIEW_PASSED"
    rows, evals = _history(db_path)
    assert rows[-1][1] == "INDEPENDENT_REVIEW_PASSED"
    assert len(seen) == 1
    independent = [row for row in evals if "independent_review" in row[2]]
    assert len(independent) == 1
    assert independent[0][0] == "deepseek-flash", "L4 记录必须追责到真实评审模型 id"
    assert independent[0][0] != WRITER_ID
    assert independent[0][1] is None, "盲评不给分,只给结论"
    assert result["independent_review"]["available"] is True
    assert result["independent_review"]["prompt"] == review_channel.REVIEW_PROMPT


def test_same_id_reviewer_cannot_claim_l4_pass(tmp_path, monkeypatch):
    """T-04 的红线:评审模型与写作模型同源 ⇒ 不得算 L4 通过(不许为了过门禁改判)。"""

    def review_call(draft):
        return _outcome(WRITER_ID, "写得不错,可以发布。")

    db_path, result = _run(tmp_path, monkeypatch, review_call=review_call)
    assert result["model_meta"]["same_model_review"] is True
    assert result["review_account"] == "AUTO_PASSED"
    rows, _ = _history(db_path)
    assert rows[-1][1] == "AUTO_PASSED"
    assert "独立性受限" in result["model_meta"]["reviewer_limitation"]


def test_missing_writer_id_keeps_independence_unproven(tmp_path, monkeypatch):
    def review_call(draft):
        return _outcome("deepseek-flash", "可以发布。")

    _, result = _run(tmp_path, monkeypatch, review_call=review_call, served_model=None)
    assert result["model_meta"]["same_model_review"] is True, "拿不到写作侧 id 就不许宣称独立"
    assert result["review_account"] == "AUTO_PASSED"


def test_blocking_verdict_adds_critical_and_rejects(tmp_path, monkeypatch):
    def review_call(draft):
        return _outcome("deepseek-flash", "两处数字与来源不符,不建议发布。")

    db_path, result = _run(tmp_path, monkeypatch, review_call=review_call)
    assert result["passed"] is False and result["status"] == "rejected"
    assert result["review_account"] == "REJECTED"
    assert any("独立评审判定不通过" in item for item in result["critical"]), result["critical"]
    rows, _ = _history(db_path)
    assert rows[-1][1] == "REJECTED"


def test_verdict_without_publishable_wording_gives_no_independent_label(tmp_path, monkeypatch):
    def review_call(draft):
        return _outcome("deepseek-flash", "整体读起来还行,细节你自己看。")

    db_path, result = _run(tmp_path, monkeypatch, review_call=review_call)
    assert result["passed"] is True
    assert result["model_meta"]["same_model_review"] is False
    assert result["review_account"] == "AUTO_PASSED"
    rows, _ = _history(db_path)
    assert rows[-1][1] == "AUTO_PASSED"


def test_rejected_draft_never_costs_a_review(tmp_path, monkeypatch):
    called: list = []

    def review_call(draft):
        called.append(draft)
        return _outcome("deepseek-flash", "可以发布。")

    _, result = _run(
        tmp_path,
        monkeypatch,
        review_call=review_call,
        review_texts=['{"score": 0.2, "issues": ["结构松散"], "critical": []}'],
    )
    assert result["passed"] is False
    assert called == [], "门禁已拦下的稿子不再花评审配额"
    assert result["review_account"] == "REJECTED"


def test_unavailable_channel_leaves_auto_pass_and_records_error(tmp_path, monkeypatch):
    def review_call(draft):
        raise review_channel.ReviewUnavailable("独立评审端点探测失败:OSError")

    db_path, result = _run(tmp_path, monkeypatch, review_call=review_call)
    assert result["passed"] is True
    assert result["model_meta"]["same_model_review"] is True
    assert "独立评审通道不可用" in result["model_meta"]["reviewer_limitation"]
    assert result["review_account"] == "AUTO_PASSED"
    assert result["independent_review"]["available"] is False
    rows, evals = _history(db_path)
    assert rows[-1][1] == "AUTO_PASSED"
    assert not [row for row in evals if "independent_review" in row[2]], "通道不可用不得留评审行"


def test_channel_off_by_default_for_offline_callers(tmp_path, monkeypatch):
    _, result = _run(tmp_path, monkeypatch, independent_review=False)
    assert result["model_meta"]["same_model_review"] is True
    assert result["independent_review"]["attempted"] is False
    assert result["review_account"] == "AUTO_PASSED"


def test_production_config_points_at_an_independent_endpoint():
    section = review_channel.review_config()
    assert section.get("enabled") is True
    assert section["base_url"].startswith("https://")
    assert section["base_url"] != cfg.load_config()["strata"]["base_url"], "评审端点必须与写作端点不同"
    assert section["api_key_file"].startswith(".secrets/"), "密钥文件只能在 .secrets/ 下"
    assert "prompt" not in section
