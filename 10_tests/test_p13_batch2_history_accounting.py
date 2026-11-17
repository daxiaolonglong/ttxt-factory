"""P13 批次2(账本 T-05 中**不需要改表结构**的那部分):写作历史的版本账目与出稿模型留痕。

P13 报告实测两处失真,本轮用现场复现再次确认:
- `writing_history` 的 status 全是 passed、全表 0 行 rejected:表主键是 (draft_id, version),
  旧代码却用 `WHERE draft_id=?` 把最终状态刷到该草稿的**所有**版本行,被门禁拦下的版本也显示 passed;
- `model_id` 全部 NULL:这一列从建表起就存在,只是写作链路调用 `_save_version(..., None)`。

两处都只改写入逻辑,不动表结构(「禁止动表结构」红线仍然有效)。
历史行的 NULL 与虚高状态**不做回填**——当时的真实出稿模型无从考证,编造等于伪造数据。
"""

import sqlite3

from ttxt.analysis import rag
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem
from ttxt.writing import workflow


def _prepared_db(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "a.db")
    store.insert_items(
        "demo",
        "test",
        [ExtractedItem(
            title="人工智能与开源",
            content="人工智能正在改变世界,开源模型让研究更高效。",
            excerpt="",
            author="",
            published_at=None,
            url="https://example.com/1",
        )],
        db_path=db_path,
    )
    store.migrate_articles_db("demo", db_path=db_path)
    rag.rebuild_index("demo", db_path=db_path)
    return db_path


class FakeModel:
    def __init__(self, review_texts):
        self.calls = {"draft": 0, "claims": 0, "review": 0, "revise": 0}
        self.review_texts = review_texts

    def _complete(self):
        return "人工智能与开源生态\n人工智能正在改变世界,开源模型让研究更高效,这是一个完整的结束句。"

    def __call__(self, prompt, max_tokens=800, temperature=0.3, enable_thinking=False):
        if "请为主题准备写作大纲" in prompt:
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
            self.calls["revise"] += 1
            return self._complete()
        self.calls["draft"] += 1
        return self._complete()


def _rows(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT version, status, model_id FROM writing_history ORDER BY version"
        ).fetchall()
    finally:
        con.close()


def test_early_rejected_version_keeps_rejected_status(tmp_path):
    """v1 因低分被门禁拦下,v2 修订后通过:两行状态必须各归各。"""
    db_path = _prepared_db(tmp_path)
    fake = FakeModel(['{"score": 0.2, "issues": ["结构松散,论证不足"], "critical": []}'])
    result = workflow.write_article(
        "demo",
        "人工智能与开源",
        model_call=fake,
        max_revisions=1,
        db_path=db_path,
        export_dir=tmp_path / "drafts",
        gate_min_chars=0,
        gate_min_score=0.6,
    )
    assert result["passed"] is True and result["revisions"] == 1
    rows = _rows(db_path)
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][1] == "rejected", "旧实现会把 v1 也刷成 passed(版本账目虚高)"
    assert rows[1][1] == "passed"


def test_rejected_draft_marks_last_version_rejected(tmp_path):
    db_path = _prepared_db(tmp_path)
    fake = FakeModel(['{"score": 0.2, "issues": ["结构松散"], "critical": []}'])
    result = workflow.write_article(
        "demo",
        "人工智能与开源",
        model_call=fake,
        max_revisions=0,
        db_path=db_path,
        export_dir=tmp_path / "drafts",
        gate_min_chars=0,
        gate_min_score=0.6,
    )
    assert result["passed"] is False
    rows = _rows(db_path)
    assert len(rows) == 1 and rows[0][1] == "rejected"


def test_served_model_is_persisted_on_every_version(tmp_path, monkeypatch):
    """model_id 列早就存在,写作链路必须把实际出稿模型写进去。"""
    monkeypatch.setattr(
        workflow,
        "_model_meta",
        lambda model_call: {
            "same_model_review": True,
            "reviewer_limitation": "测试替身",
            "served_model": "unit-probe-model",
        },
    )
    db_path = _prepared_db(tmp_path)
    fake = FakeModel(['{"score": 0.2, "issues": ["结构松散"], "critical": []}'])
    workflow.write_article(
        "demo",
        "人工智能与开源",
        model_call=fake,
        max_revisions=1,
        db_path=db_path,
        export_dir=tmp_path / "drafts",
        gate_min_chars=0,
        gate_min_score=0.6,
    )
    rows = _rows(db_path)
    assert [r[0] for r in rows] == [1, 2]
    assert {r[2] for r in rows} == {"unit-probe-model"}
