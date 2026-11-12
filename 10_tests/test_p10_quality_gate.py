"""P10 写作质量门禁测试:截断拒绝、低分拒绝、critical 拒绝、修订耗尽拒绝、通过路径、quote 实体化。"""

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


class FakeModel:
    def __init__(self, draft_texts=None, review_texts=None, revise_texts=None):
        self.calls = {"outline": 0, "draft": 0, "claims": 0, "review": 0, "revise": 0}
        self.draft_texts = draft_texts or []
        self.review_texts = review_texts or []
        self.revise_texts = revise_texts or []

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
            index = self.calls["revise"]
            self.calls["revise"] += 1
            if index < len(self.revise_texts):
                return self.revise_texts[index]
            return self._complete()
        index = self.calls["draft"]
        self.calls["draft"] += 1
        if index < len(self.draft_texts):
            return self.draft_texts[index]
        return self._complete()


def _run(tmp_path, fake, max_revisions=0):
    db_path = _prepared_db(tmp_path)
    result = workflow.write_article(
        "demo",
        "人工智能与开源",
        model_call=fake,
        max_revisions=max_revisions,
        db_path=db_path,
        export_dir=tmp_path / "drafts",
        gate_min_chars=0,
        gate_min_score=0.6,
    )
    return db_path, result


def _assert_rejected_in_db(db_path, draft_id):
    con = sqlite3.connect(db_path)
    try:
        status = con.execute(
            "SELECT status FROM writing_history WHERE draft_id=?", (draft_id,)
        ).fetchone()[0]
        assert status == "rejected"
    finally:
        con.close()


def test_truncated_draft_rejected_and_not_exported(tmp_path):
    # 自动加大 token 重试一次后仍然截断 → 必须拒绝
    fake = FakeModel(
        draft_texts=[
            "人工智能\n正文内容被截断,还没写完,只需",
            "人工智能\n再次生成的正文仍然被截断,只",
        ]
    )
    db_path, result = _run(tmp_path, fake)
    assert result["passed"] is False
    assert result["status"] == "rejected"
    assert result["truncated"] is True
    path = Path(result["path"])
    assert "rejected" in str(path)
    main = tmp_path / "drafts" / "demo" / f"{result['draft_id']}.md"
    assert not main.exists()
    _assert_rejected_in_db(db_path, result["draft_id"])


def test_low_score_rejected(tmp_path):
    fake = FakeModel(review_texts=['{"score": 0.4, "issues": ["论证不足"], "critical": []}'])
    db_path, result = _run(tmp_path, fake)
    assert result["passed"] is False
    assert result["status"] == "rejected"
    assert "低于门禁" in (result["notes"] or "")
    _assert_rejected_in_db(db_path, result["draft_id"])


def test_critical_issue_rejected(tmp_path):
    fake = FakeModel(review_texts=['{"score": 0.8, "issues": [], "critical": ["存在无来源断言"]}'])
    db_path, result = _run(tmp_path, fake)
    assert result["passed"] is False
    assert result["critical"]
    _assert_rejected_in_db(db_path, result["draft_id"])


def test_revision_exhausted_rejected(tmp_path):
    fake = FakeModel(
        review_texts=[
            '{"score": 0.4, "issues": ["论证不足"], "critical": []}',
            '{"score": 0.4, "issues": ["仍论证不足"], "critical": []}',
        ]
    )
    db_path, result = _run(tmp_path, fake, max_revisions=1)
    assert result["passed"] is False
    assert result["revisions"] == 1
    _assert_rejected_in_db(db_path, result["draft_id"])


def test_revision_fixes_issues_and_passes(tmp_path):
    fake = FakeModel(
        review_texts=['{"score": 0.5, "issues": ["论证不足"], "critical": []}'],
        revise_texts=["人工智能与开源生态\n修改后的完整正文,以句号结束。"],
    )
    _, result = _run(tmp_path, fake, max_revisions=1)
    assert result["passed"] is True
    assert result["revisions"] == 1
    assert result["versions"] == 2


def test_pass_flow_exports_quote_and_status(tmp_path):
    fake = FakeModel()
    db_path, result = _run(tmp_path, fake)
    assert result["passed"] is True
    assert result["status"] == "passed"
    assert result["truncated"] is False
    path = Path(result["path"])
    assert "rejected" not in str(path)
    assert path.exists()
    assert result["evidence"][0]["quote"]

    con = sqlite3.connect(db_path)
    try:
        status = con.execute("SELECT status FROM writing_history").fetchone()[0]
        assert status == "passed"
        quote = con.execute("SELECT quote FROM evidence_links").fetchone()[0]
        assert quote
    finally:
        con.close()
