"""P40 独立评审通道(账本 T-04/T-05)。

要证明的不是"能跑",而是四条红线:
1. 提示词只有 6 个字「审评这篇文章」+ 正文,不带评分细则,不做成配置项(避免被写作侧口径诱导);
2. 密钥只从环境变量/文件读,绝不进回执、日志、DB、异常文本;
3. **评审模型与写作模型同源 ⇒ 不得算 L4 通过**(`same_model_review` 仍为 True,版本账目不得给
   INDEPENDENT_REVIEW_PASSED),独立性只按两侧真实 id 比较;
4. 独立评审只能收紧门禁:判阻断就拒绝,通道不可用就按"未解除同源"处理,不得当成通过。
5. R51 端点策略拆分:写作侧(strata)仍强制本地回环,配云端一律显式失败;评审侧允许配置里的
   云端 https 端点,但缺 key / 不可达 / 明文 http 都显式失败,回执如实记 reviewer_endpoint,
   绝不静默降级回本地、绝不悄悄换模型。
"""

import json
import sqlite3
from urllib.parse import urlparse

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
# R44:评审已强制走本地模型,模型 id 从配置读,不再写死云端名
REVIEW_MODEL = str(review_channel.review_config().get("model_id") or "")


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


def test_real_world_negative_wording_is_blocking():
    """R30 现场口径:评审写「暂不建议直接发表」「建议大修」,旧字面标记漏判 ⇒ 必须识别为阻断。"""
    for text in (
        "若作为公开发表文章,**暂不建议直接发表**;若作为课程评论,经修改后仍有潜力。",
        "**审稿结论:建议大修(Major Revision)。**",
        "综合考虑,不宜立即发表。",
        "这篇文章不适合刊发。",
    ):
        verdict = review_channel.verdict_markers(text)
        assert verdict["blocking"] is True and verdict["publishable"] is False, text
    assert review_channel.verdict_markers("修改后可以直接发表。")["publishable"] is True


# --------------------------------------------------------------------------- 密钥红线


def test_missing_key_raises_without_leaking_anything(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    with pytest.raises(review_channel.ReviewUnavailable) as exc:
        review_channel.api_key({"api_key_env": "DEEPSEEK_API_KEY", "api_key_file": ".secrets/gone"})
    assert "DEEPSEEK_API_KEY" in str(exc.value)


def test_empty_key_file_is_treated_as_missing(tmp_path, monkeypatch):
    """空文件不能当密钥:required=True 必须显式抛错,不许无凭据发云端请求。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    secret = tmp_path / ".secrets"
    secret.mkdir()
    (secret / "deepseek_api_key").write_text("\n", encoding="utf-8")
    section = {"api_key_env": "DEEPSEEK_API_KEY", "api_key_file": ".secrets/deepseek_api_key"}
    with pytest.raises(review_channel.ReviewUnavailable):
        review_channel.api_key(section)
    assert review_channel.api_key(section, required=False) == ""


def test_key_reads_env_then_file_without_network(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("STRATA_API_KEY", raising=False)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    secret = tmp_path / ".secrets"
    secret.mkdir()
    (secret / "deepseek_api_key").write_text(SENTINEL + "\n", encoding="utf-8")
    assert review_channel.api_key({"api_key_file": ".secrets/deepseek_api_key"}) == SENTINEL
    monkeypatch.setenv("STRATA_API_KEY", "from-env")
    assert review_channel.api_key({"api_key_file": ".secrets/deepseek_api_key"}) == "from-env"


def test_review_request_carries_only_prompt_and_draft_and_no_secret(monkeypatch):
    monkeypatch.setenv("STRATA_API_KEY", SENTINEL)
    captured: dict = {}

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return {"model": REVIEW_MODEL, "text": "论证尚可,可以发布。", "finish_reason": "stop",
                "usage": {"completion_tokens": 7}, "elapsed_s": 0.2}

    monkeypatch.setattr(model_client, "models", lambda **kw: [REVIEW_MODEL])
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
    assert captured["model_id"] == REVIEW_MODEL
    assert captured["base_url"] == review_channel.review_config()["base_url"]
    assert outcome["reviewer_model"] == REVIEW_MODEL
    assert outcome["verdict"]["publishable"] is True
    blob = json.dumps(outcome, ensure_ascii=False) + repr(logs)
    assert SENTINEL not in blob, "密钥不得进返回值或日志"
    assert captured["max_tokens"] >= 4000, "推理模型 max_tokens 含推理链,给小了正文会是空的"


def test_unlisted_model_and_dead_endpoint_both_mean_unavailable(monkeypatch):
    monkeypatch.setenv("STRATA_API_KEY", SENTINEL)
    monkeypatch.setattr(model_client, "models", lambda **kw: ["some-other-model"])
    with pytest.raises(review_channel.ReviewUnavailable) as exc:
        review_channel.served_model()
    assert REVIEW_MODEL in str(exc.value)

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


CLOUDY = {
    "enabled": True,
    "base_url": "https://api.deepseek.com",
    "model_id": "deepseek-chat",
    "api_key_env": "DEEPSEEK_API_KEY",
}


def test_writer_side_non_loopback_endpoint_is_refused(monkeypatch):
    """R51 红线一:写作侧仍强制回环,strata 配置指到云端时一律显式失败,不发请求。"""
    monkeypatch.setattr(cfg, "load_config", lambda: {"strata": {"base_url": "https://api.deepseek.com"}})
    with pytest.raises(model_client.ModelError) as exc:
        model_client._base_url()
    assert "写作侧" in str(exc.value) and "回环" in str(exc.value)
    with pytest.raises(model_client.ModelError):
        model_client.models()  # 探测也不许发出去
    with pytest.raises(model_client.ModelError):
        model_client.chat([{"role": "user", "content": "hi"}], model_id="anything")
    # 评审通道显式传端点,不受写作侧策略牵连
    assert model_client._base_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080"
    assert model_client.require_loopback("http://localhost:8080") == "http://localhost:8080"


def test_review_side_allows_configured_cloud_endpoint(monkeypatch):
    """R51 红线二:评审侧允许配置里的云端端点,回执与请求的端点都是云端,绝不写成本地。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL)
    captured: dict = {}

    def fake_models(**kwargs):
        captured["probe_base_url"] = kwargs.get("base_url")
        captured["probe_api_key"] = kwargs.get("api_key")
        return ["deepseek-chat", "deepseek-reasoner"]

    def fake_chat(messages, **kwargs):
        captured.update(kwargs)
        return {"model": kwargs.get("model_id"), "text": "论证尚可,可以发布。",
                "finish_reason": "stop", "usage": {"completion_tokens": 7}, "elapsed_s": 0.2}

    monkeypatch.setattr(model_client, "models", fake_models)
    monkeypatch.setattr(model_client, "chat", fake_chat)

    outcome = review_channel.review("正文第一行\n正文第二行", section=CLOUDY)

    assert captured["probe_base_url"] == "https://api.deepseek.com"
    assert captured["base_url"] == "https://api.deepseek.com", "评审请求绝不被改写回回环地址"
    assert captured["api_key"] == SENTINEL, "云端评审必须带配置的 key,不无凭据发请求"
    assert captured["model_id"] == "deepseek-chat", "端点多模型时按配置 id 请求,不悄悄换模型"
    assert outcome["reviewer_endpoint"] == "https://api.deepseek.com"
    assert outcome["reviewer_is_local"] is False
    assert outcome["reviewer_model"] == "deepseek-chat"
    blob = json.dumps(outcome, ensure_ascii=False)
    assert SENTINEL not in blob, "密钥不得进回执"
    assert "127.0.0.1" not in blob and "localhost" not in blob, "云端评审不得被记成本地端点"


def test_review_side_cloud_without_key_fails_without_network(monkeypatch):
    """缺 key 时显式抛错:不静默降级回本地,也不发起任何请求。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def no_network(**kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("缺 key 不得向云端发起请求")

    monkeypatch.setattr(model_client, "models", no_network)
    monkeypatch.setattr(model_client, "chat", no_network)
    with pytest.raises(review_channel.ReviewUnavailable) as exc:
        review_channel.review("正文", section=CLOUDY)
    assert "DEEPSEEK_API_KEY" in str(exc.value)
    assert SENTINEL not in str(exc.value)


def test_review_side_cloud_endpoint_must_be_https_and_well_formed(monkeypatch):
    def no_network(**kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("不合规端点不得发起请求")

    monkeypatch.setattr(model_client, "models", no_network)
    monkeypatch.setattr(model_client, "chat", no_network)
    with pytest.raises(review_channel.ReviewUnavailable) as exc:
        review_channel.review("正文", section={**CLOUDY, "base_url": "http://api.deepseek.com"})
    assert "https" in str(exc.value), "云端评审走明文 http 必须被拒(密钥不经明文传输)"
    with pytest.raises(review_channel.ReviewUnavailable):
        review_channel.review("正文", section={**CLOUDY, "base_url": ""})
    with pytest.raises(review_channel.ReviewUnavailable):
        review_channel.review("正文", section={**CLOUDY, "base_url": "not-a-url"})
    # 回环端点仍然直接放行(本地评审不受云端策略影响)
    base_url, is_local = review_channel._review_endpoint({"base_url": "http://127.0.0.1:8080"})
    assert base_url == "http://127.0.0.1:8080" and is_local is True


def test_review_side_unreachable_cloud_fails_not_silent_downgrade(monkeypatch):
    """端点不可达 ⇒ 显式 ReviewUnavailable,不降级回本地、不悄悄换模型。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL)

    def boom(**kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(model_client, "models", boom)
    with pytest.raises(review_channel.ReviewUnavailable) as exc:
        review_channel.served_model(CLOUDY)
    assert "探测失败" in str(exc.value)
    assert SENTINEL not in str(exc.value)


# --------------------------------------------------------------------------- 写作链路:独立性判定


def test_different_reviewer_id_establishes_independence(tmp_path, monkeypatch):
    seen: list = []

    def review_call(draft):
        seen.append(draft)
        return _outcome("deepseek-flash", "结构与事实基本一致,可以发布。")

    db_path, result = _run(tmp_path, monkeypatch, review_call=review_call)
    assert result["model_meta"]["same_model_review"] is False
    assert result["model_meta"]["reviewer_model"] == "deepseek-flash"
    assert result["review_account"] == "independent_review_passed"
    assert result["review_account_basis"] == "independent_review_approved"
    rows, evals = _history(db_path)
    assert rows[-1][1] == "independent_review_passed"
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
    assert result["review_account"] == "auto_passed"
    assert result["review_account_basis"] == "same_model_review"
    rows, _ = _history(db_path)
    assert rows[-1][1] == "auto_passed"
    assert "独立性受限" in result["model_meta"]["reviewer_limitation"]


def test_missing_writer_id_keeps_independence_unproven(tmp_path, monkeypatch):
    def review_call(draft):
        return _outcome("deepseek-flash", "可以发布。")

    _, result = _run(tmp_path, monkeypatch, review_call=review_call, served_model=None)
    assert result["model_meta"]["same_model_review"] is True, "拿不到写作侧 id 就不许宣称独立"
    assert result["review_account"] == "auto_passed"


def test_blocking_verdict_adds_critical_and_rejects(tmp_path, monkeypatch):
    def review_call(draft):
        return _outcome("deepseek-flash", "两处数字与来源不符,不建议发布。")

    db_path, result = _run(tmp_path, monkeypatch, review_call=review_call)
    assert result["passed"] is False and result["status"] == "rejected"
    assert result["review_account"] == "rejected"
    assert result["review_account_basis"] == "independent_review_blocked"
    assert any("独立评审判定不通过" in item for item in result["critical"]), result["critical"]
    rows, _ = _history(db_path)
    assert rows[-1][1] == "rejected"


def test_revision_required_wording_rejects_draft(tmp_path, monkeypatch):
    """R30 真实评审原文:旧标记漏判把「暂不建议直接发表」记成 AUTO_PASSED,现必须拒绝且依据可查。"""

    def review_call(draft):
        return _outcome(
            "deepseek-flash",
            "综合评分 62-68/100。**审稿结论:建议大修(Major Revision)**;若作为公开发表文章,"
            "**暂不建议直接发表**。",
        )

    db_path, result = _run(tmp_path, monkeypatch, review_call=review_call)
    assert result["passed"] is False and result["status"] == "rejected"
    assert result["review_account"] == "rejected"
    assert result["review_account_basis"] == "independent_review_blocked"
    rows, _ = _history(db_path)
    assert rows[-1][1] == "rejected"


def test_verdict_without_publishable_wording_gives_no_independent_label(tmp_path, monkeypatch):
    def review_call(draft):
        return _outcome("deepseek-flash", "整体读起来还行,细节你自己看。")

    db_path, result = _run(tmp_path, monkeypatch, review_call=review_call)
    assert result["passed"] is True
    assert result["model_meta"]["same_model_review"] is False
    assert result["review_account"] == "auto_passed"
    assert result["review_account_basis"] == "independent_review_not_approved"
    rows, _ = _history(db_path)
    assert rows[-1][1] == "auto_passed"


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
    assert result["review_account"] == "rejected"


def test_unavailable_channel_leaves_auto_pass_and_records_error(tmp_path, monkeypatch):
    def review_call(draft):
        raise review_channel.ReviewUnavailable("独立评审端点探测失败:OSError")

    db_path, result = _run(tmp_path, monkeypatch, review_call=review_call)
    assert result["passed"] is True
    assert result["model_meta"]["same_model_review"] is True
    assert "独立评审通道不可用" in result["model_meta"]["reviewer_limitation"]
    assert result["review_account"] == "auto_passed"
    assert result["review_account_basis"] == "independent_review_unavailable"
    rows, evals = _history(db_path)
    assert rows[-1][1] == "auto_passed"
    assert not [row for row in evals if "independent_review" in row[2]], "通道不可用不得留评审行"


def test_channel_off_by_default_for_offline_callers(tmp_path, monkeypatch):
    _, result = _run(tmp_path, monkeypatch, independent_review=False)
    assert result["model_meta"]["same_model_review"] is True
    assert result["independent_review"]["attempted"] is False
    assert result["review_account"] == "auto_passed"
    assert result["review_account_basis"] == "independent_review_not_attempted"


def test_production_writer_endpoint_is_loopback():
    """R51:写作侧(strata)线上配置必须是本地回环端点,云端地址不得出现在 strata 段。"""
    section = cfg.load_config().get("strata", {})
    host = urlparse(str(section.get("base_url") or "")).hostname
    assert host in model_client.LOCAL_HOSTS, f"写作端点必须是本地回环地址,实际 {section.get('base_url')}"
    model_client.require_loopback(str(section.get("base_url") or ""))


def test_production_review_endpoint_is_policy_compliant():
    """R51:评审端点允许回环或 https 云端;不得是明文 http 云端,且不得做成提示词配置。"""
    section = review_channel.review_config()
    assert section.get("enabled") is True
    review_channel._review_endpoint(section)  # 不合规直接抛
    assert "prompt" not in section
