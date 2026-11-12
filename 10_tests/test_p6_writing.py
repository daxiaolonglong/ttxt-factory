"""P6 测试:写作闭环(假模型)、解析容错、修订路径、导出与数据库记录。

P10 起写作加入 L4 门禁:完整、评分 ≥0.6、无 critical 才导出为成品。
"""

import sqlite3
from pathlib import Path

from ttxt.analysis import rag
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem
from ttxt.writing import workflow


def _item(title: str, content: str, url: str) -> ExtractedItem:
    return ExtractedItem(
        title=title,
        content=content,
        excerpt="",
        author="",
        published_at=None,
        url=url,
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


def _fake_model(state: dict, review_sequences: list[str] | None = None):
    def call(prompt, max_tokens=800, temperature=0.3, enable_thinking=False):
        if "请为主题准备写作大纲" in prompt:
            return "- 背景\n- 核心观点\n- 结语"
        if "关键事实陈述" in prompt:
            return '[{"claim": "人工智能正在改变世界", "article_id": 1}]'
        if "严格的中文内容评审" in prompt:
            state["reviews"] += 1
            if review_sequences and state["reviews"] <= len(review_sequences):
                return review_sequences[state["reviews"] - 1]
            return '{"score": 0.8, "issues": [], "critical": []}'
        if "根据评审意见修改文章" in prompt:
            return "修改后的标题\n修改后的正文,人工智能正在改变世界。"
        return "人工智能与开源\n人工智能正在改变世界,开源模型让研究更高效。"

    return call


def test_parse_claims():
    assert workflow.parse_claims('```json\n[{"claim":"A","article_id":1}]\n```') == [
        {"claim": "A", "article_id": 1}
    ]
    assert workflow.parse_claims('前缀 [{"claim":"B","article_id":0}] 后缀') == [
        {"claim": "B", "article_id": 0}
    ]
    assert workflow.parse_claims("无法解析") == []


def test_parse_review():
    review = workflow.parse_review(
        '```json\n{"score": 0.7, "issues": ["问题A"], "critical": ["存在无来源断言"]}\n```'
    )
    assert review["score"] == 0.7
    assert review["issues"] == ["问题A"]
    assert review["critical"] == ["存在无来源断言"]
    fallback = workflow.parse_review("评分 0.5 分")
    assert fallback["score"] == 0.5
    assert fallback["critical"] == []


def test_parse_review_critical_hints():
    review = workflow.parse_review('{"score": 0.8, "issues": ["部分内容夸大"]}')
    assert review["critical"] == ["部分内容夸大"]


def test_write_workflow(tmp_path):
    db_path = _prepared_db(tmp_path)
    state = {"reviews": 0}
    result = workflow.write_article(
        "demo",
        "人工智能与开源",
        model_call=_fake_model(state),
        max_revisions=0,
        db_path=db_path,
        export_dir=tmp_path / "drafts",
        gate_min_chars=0,
    )
    assert result["versions"] == 1
    assert result["materials"] == 1
    assert result["evidence"][0]["credibility"] == "verified"
    assert result["evidence"][0]["quote"]
    assert result["passed"] is True
    assert result["status"] == "passed"

    path = Path(result["path"])
    assert path.exists()
    assert "rejected" not in str(path)
    text = path.read_text(encoding="utf-8")
    assert "待人工确认" in text
    assert "事实证据链" in text

    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM writing_history").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM evaluation_history").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM evidence_links").fetchone()[0] == 1
        status = con.execute("SELECT status FROM writing_history").fetchone()[0]
        assert status == "passed"
    finally:
        con.close()


def test_write_workflow_revision(tmp_path):
    db_path = _prepared_db(tmp_path)
    state = {"reviews": 0}
    fake = _fake_model(state, ['{"score": 0.5, "issues": ["论证不足"], "critical": []}'])
    result = workflow.write_article(
        "demo",
        "人工智能与开源",
        model_call=fake,
        max_revisions=1,
        db_path=db_path,
        export_dir=tmp_path / "drafts",
        gate_min_chars=0,
    )
    assert result["versions"] == 2
    assert result["revisions"] == 1
    assert result["score"] == 0.8
    assert result["passed"] is True

    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM writing_history").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM evaluation_history").fetchone()[0] == 2
    finally:
        con.close()
