"""P12 批次4:确定性事实核验(verifier)与写作链集成测试。

覆盖:原文连续引句绑定(可复核 offset)、数字/日期/人物/机构/因果分维度核验、
事实-观点分离、标题兑现、无来源断言、旧库列迁移、修订后证据重算、
同模型评审限制记录。
"""

import json
import sqlite3
from pathlib import Path

import pytest

from ttxt.analysis import rag
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem
from ttxt.writing import verifier, workflow

SRC_URL = "https://example.com/src/42"
SRC_TEXT = (
    "2026年3月,星海科技公司发布新一代开源模型。"
    "王芳表示,该模型训练成本为 25 亿元,推理速度提升 3.5%。"
    "由于采用了稀疏注意力架构,长文本处理成本大幅下降。"
)
ARTICLE = {"id": 7, "title": "星海科技发布新模型", "url": SRC_URL, "text": SRC_TEXT}


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


# --------------------------------------------------------------------------- 引句绑定


def test_quote_is_contiguous_slice_with_verbatim_recheck():
    ev = verifier.verify_claim("该模型训练成本为 25 亿元", ARTICLE)
    assert ev["credibility"] == "verified"
    assert ev["source_url"] == SRC_URL
    assert ev["quote"] and ev["quote_offset"] is not None
    assert verifier.quote_verbatim(ev["quote"], ev["quote_offset"], SRC_TEXT)
    assert SRC_TEXT[ev["quote_offset"] : ev["quote_offset"] + len(ev["quote"])] == ev["quote"]


def test_fullwidth_number_source_still_binds_verbatim():
    text = "报告称,营收增长３.５个百分点。"
    article = {"id": 1, "title": "t", "url": "https://e.com/2", "text": text}
    ev = verifier.verify_claim("营收增长3.5个百分点", article)
    assert ev["credibility"] == "verified"
    assert verifier.quote_verbatim(ev["quote"], ev["quote_offset"], text)


def test_unrelated_source_not_bound():
    ev = verifier.verify_claim("台风登陆海南造成航班大面积取消", ARTICLE)
    assert ev["credibility"] == "unverified"
    assert ev["quote"] is None


# --------------------------------------------------------------------------- 分维度扰动


def test_number_perturbation_unverified():
    ev = verifier.verify_claim("王芳表示,该模型训练成本为 32 亿元", ARTICLE)
    assert ev["credibility"] == "unverified"
    assert ev["checks"]["numbers"]["missing"]

    issues, critical = verifier.deterministic_findings([ev], "标题", "正文")
    assert any("事实未通过来源核验" in c for c in critical)


def test_date_perturbation_unverified():
    ev = verifier.verify_claim("2025年3月,星海科技公司发布新一代开源模型", ARTICLE)
    assert ev["credibility"] == "unverified"
    assert ev["checks"]["dates"]["missing"]


def test_person_and_org_perturbation_unverified():
    ev = verifier.verify_claim("李明辉表示,昆仑智能研究院发布了新模型", ARTICLE)
    assert ev["credibility"] == "unverified"
    assert ev["checks"]["persons"]["missing"] == ["李明辉"]
    assert any("昆仑" in item for item in ev["checks"]["orgs"]["missing"])


def test_causal_connector_absent_from_bound_quote_unverified():
    # 「导致」在来源任何引句中都不共现 → 因果维度必须缺失
    ev = verifier.verify_claim("长文本处理成本大幅下降导致收入增长", ARTICLE)
    assert ev["credibility"] == "unverified"
    causal = ev["checks"]["causal"]
    assert "导致" in causal["missing"]
    assert "不验证因果方向" in causal["note"]


def test_causal_note_records_direction_limit():
    ev = verifier.verify_claim("由于采用了稀疏注意力架构,成本大幅下降", ARTICLE)
    assert ev["credibility"] == "verified"
    note = ev["checks"]["causal"]["note"]
    assert "不验证因果方向" in note


# --------------------------------------------------------------------------- 观点分离 / 无来源


def test_opinion_separated_from_fact_gate():
    ev = verifier.verify_claim("我认为该模型或将改变行业格局", ARTICLE)
    assert ev["kind"] == "opinion"
    assert ev["credibility"] == "opinion"
    assert ev["checks"]["opinion_markers"]
    issues, critical = verifier.deterministic_findings([ev], "该模型", "该模型或将改变行业格局")
    assert critical == []  # 观点不参与事实门禁


def test_hedge_words_do_not_collide_with_common_phrases():
    # 「一个人/评论员」类短语不得被当作观点标记
    assert verifier.is_opinion("60个人直接向一个人汇报") == []
    assert verifier.is_opinion("该新闻的评论区有讨论") == []
    assert verifier.is_opinion("我个人认为这是趋势") != []


def test_no_source_claim_is_critical():
    ev = verifier.verify_claim("某机构称市场规模达 999 亿元", None)
    assert ev["credibility"] == "no_source"
    _, critical = verifier.deterministic_findings([ev], "标题", "正文正文正文")
    assert any(c.startswith("无来源断言") for c in critical)


# --------------------------------------------------------------------------- 标题兑现


def test_title_payoff_missing_number_fails():
    check = verifier.check_title("2026年成本仅 5 亿元", "文章讲的是模型架构与开源生态。" * 4)
    assert not check["ok"]
    assert check["missing"].get("numbers")


def test_title_payoff_ok_when_covered():
    title = "星海科技发布新开源模型"
    body = "星海科技公司发布新一代开源模型,王芳介绍了训练细节。"
    assert verifier.check_title(title, body)["ok"]


# --------------------------------------------------------------------------- 列迁移(旧库)


def test_old_db_gains_evidence_columns_idempotent(tmp_path):
    db_path = tmp_path / "old.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        "CREATE TABLE evidence_links ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id TEXT NOT NULL, claim TEXT NOT NULL,"
        "article_id INTEGER, quote TEXT, overlap REAL, credibility TEXT, created_at TEXT NOT NULL);"
    )
    con.commit()
    con.close()
    store.migrate_articles_db("demo", db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)  # 幂等
    con = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(evidence_links)")}
        assert {"source_url", "checks_json"} <= cols
    finally:
        con.close()


# --------------------------------------------------------------------------- 写作链集成


class ReviseModel:
    """首轮草稿证据正常;修订稿换掉了事实 → 必须重新核验。"""

    def __init__(self):
        self.reviews = 0

    def __call__(self, prompt, max_tokens=800, temperature=0.3, enable_thinking=False):
        if "请为主题准备写作大纲" in prompt:
            return "- 背景\n- 结语"
        if "关键事实陈述" in prompt:
            if "88 亿元" in prompt:
                return '[{"claim": "该模型营收达到 88 亿元", "article_id": 1}]'
            return '[{"claim": "人工智能正在改变世界", "article_id": 1}]'
        if "严格的中文内容评审" in prompt:
            self.reviews += 1
            if self.reviews == 1:
                return '{"score": 0.5, "issues": ["论证不足"], "critical": []}'
            return '{"score": 0.9, "issues": [], "critical": []}'
        if "根据评审意见修改文章" in prompt:
            return "人工智能与开源\n修订后正文:该模型营收达到 88 亿元。"
        return "人工智能与开源\n人工智能正在改变世界,开源模型让研究更高效,这是一个完整的结束句。"


def test_write_evidence_persists_url_checks_and_reverifies_after_revision(tmp_path):
    db_path = _prepared_db(tmp_path)
    result = workflow.write_article(
        "demo",
        "人工智能与开源",
        model_call=ReviseModel(),
        max_revisions=1,
        db_path=db_path,
        export_dir=tmp_path / "drafts",
        gate_min_chars=0,
        gate_min_score=0.6,
    )
    # 修订后证据必须重算:旧声明不应残留,新声明因数字不符被致命拒绝
    assert result["revisions"] == 1
    assert result["passed"] is False
    assert result["status"] == "rejected"
    assert any("事实未通过来源核验" in c for c in result["critical"])
    assert result["model_meta"]["same_model_review"] is True
    assert "独立性受限" in result["model_meta"]["reviewer_limitation"]
    assert result["model_meta"]["served_model"] is None  # 注入模型,不探测
    assert "88" in (result["notes"] or "") or "致命" in (result["notes"] or "")

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT claim, source_url, checks_json, quote FROM evidence_links WHERE draft_id = ?",
            (result["draft_id"],),
        ).fetchall()
        assert len(rows) == 1  # 首轮证据已被替换,不是叠加
        claim, url, checks_json, quote = rows[0]
        assert claim == "该模型营收达到 88 亿元"
        assert url == "https://example.com/1"
        checks = json.loads(checks_json)
        assert any("88" in item for item in checks["checks"]["numbers"]["missing"])
        content = con.execute("SELECT content FROM articles WHERE url = ?", (url,)).fetchone()[0]
        offset = checks["quote_offset"]
        if quote is not None:
            assert content[offset : offset + len(quote)] == quote  # 入库的 offset 可复核
        ev_rows = con.execute(
            "SELECT criteria_json FROM evaluation_history WHERE draft_id=? AND version=2",
            (result["draft_id"],),
        ).fetchall()
        assert "same_model_review" in ev_rows[0][0]
    finally:
        con.close()

    path = Path(result["path"])
    text = path.read_text(encoding="utf-8")
    assert "https://example.com/1" in text  # 导出报告含来源 URL
    assert "连续切片" in text  # 导出报告含原文连续引句列


def test_default_gate_evidence_has_verbatim_quote_and_url(tmp_path):
    db_path = _prepared_db(tmp_path)
    result = workflow.write_article(
        "demo",
        "人工智能与开源",
        model_call=_plain_model(),
        max_revisions=0,
        db_path=db_path,
        export_dir=tmp_path / "drafts",
        gate_min_chars=0,
    )
    assert result["passed"] is True
    ev = result["evidence"][0]
    assert ev["source_url"] == "https://example.com/1"
    con = sqlite3.connect(db_path)
    try:
        art = con.execute("SELECT content FROM articles LIMIT 1").fetchone()[0]
    finally:
        con.close()
    assert verifier.quote_verbatim(ev["quote"], ev["quote_offset"], art)


def _plain_model():
    def call(prompt, max_tokens=800, temperature=0.3, enable_thinking=False):
        if "请为主题准备写作大纲" in prompt:
            return "- 背景\n- 结语"
        if "关键事实陈述" in prompt:
            return '[{"claim": "人工智能正在改变世界", "article_id": 1}]'
        if "严格的中文内容评审" in prompt:
            return '{"score": 0.8, "issues": [], "critical": []}'
        return "人工智能与开源\n人工智能正在改变世界,开源模型让研究更高效,这是一个完整的结束句。"

    return call
