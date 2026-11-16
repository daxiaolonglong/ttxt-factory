"""P25(第 8 轮,2026-11-16)测试:两处"事后无法归因"的可观测性缺口。

根因凭据(用户自己的只读周报 `08_wiki/reports/P13-weekly-20261116.md` §6,本报告不重写):

1. **入队没有日志**:job 71(`technology / toutiao_tech`,queued,created 2026-11-16T08:28:54+08:00)
   的入队来源**无法从日志归因** —— `system_log` 表 0 行,`09_logs/ttxt-*.jsonl` 全周 grep
   `enqueue / crawl_enqueue / feedback_gap` **0 命中**。P13 batch0 就记了这条缺口
   (`store.create_crawl_job` 的 INSERT 之后没有日志),批次4 规格要求补 `crawl.enqueued` 事件
   (带 job_id/source/category/actor),**至今未落地**。排队动作不可归因 = 面板、调度器、人工
   三种来源混在一起没人分得清,排队积压也无法解释。
2. **FTS 覆盖率没有体检项**:周报实测 social 33/22(−11)、technology 168/159(−9),
   缺的正好是今晨 02:25/02:47 最后一批入库的文章 —— 采集入口会自动跑清洗管线(C-4),
   但 **FTS 索引只有 `analysis/analyze.py:31 → rag.rebuild_index` 会重建**,而调度器自 11-12 起冻结,
   于是"文章在库里、检索搜不到"这种状态既没人发现也没有体检项。文章数看起来完全正常。

两条都必须**当场红**,补完才绿。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ttxt import config as cfg
from ttxt import db as ttxt_db
from ttxt import doctor
from ttxt.analysis import rag
from ttxt.crawler import store
from ttxt.crawler.clean import normalize_url


# ------------------------------------------------------------------ 1 入队要留日志


def _log_events() -> list[dict]:
    events: list[dict] = []
    log_dir = Path(cfg.LOG_DIR)
    if not log_dir.exists():
        return events
    for path in sorted(log_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def test_create_crawl_job_logs_enqueue_with_attribution(tmp_path: Path):
    """排队必须留下 `crawl.enqueued`,带 job_id/source/category 与来源 actor,否则事后无法归因。"""
    system_db = tmp_path / "system.db"
    ttxt_db.init_system_db(system_db)
    job_id = store.create_crawl_job(
        "toutiao_finance", "finance", db_path=system_db, meta={"dispatched_via": "dashboard"}
    )
    enqueued = [event for event in _log_events() if event.get("event") == "crawl.enqueued"]
    assert enqueued, "入队没有留下 crawl.enqueued 事件:排队动作无法事后归因(P13 周报 §6)"
    meta = enqueued[-1].get("meta") or {}
    assert int(meta["job_id"]) == int(job_id)
    assert meta["source"] == "toutiao_finance"
    assert meta["category"] == "finance"
    assert meta["actor"] == "dashboard", "actor 必须能区分面板 / 调度器 / 人工 / 关键词计划"


def test_enqueue_actor_distinguishes_scheduler_and_manual(tmp_path: Path):
    """同一张表里三种排队来源必须能分辨:调度器、人工 CLI、关键词计划各自不同 actor。"""
    system_db = tmp_path / "system2.db"
    ttxt_db.init_system_db(system_db)
    store.create_crawl_job("chinanews_society", "social", db_path=system_db,
                           meta={"reason": "scheduler", "dispatched_via": "scheduler"})
    store.create_crawl_job("toutiao_history", "history", db_path=system_db)
    actors = [
        (event.get("meta") or {}).get("actor")
        for event in _log_events()
        if event.get("event") == "crawl.enqueued"
    ]
    assert "scheduler" in actors
    assert actors[-1] in ("manual", "cli"), f"没有 meta 的人工入队 actor 要能认出来,实测 {actors[-1]!r}"


# ------------------------------------------------------------------ 2 FTS 覆盖率要能体检
#
# `isolated_data_root` 是把**生产 6 个类目库逐字节复制到 tmp**,再把 cfg.CATEGORIES_DIR 指过去,
# 所以体检看到的是"生产副本"(374 行、social 22/33 这些数字都来自副本),不是空库。
# 因此这里**不写死绝对数字**(生产一直在涨),只用 SQL 现读现算,断言"细节里必须出现这个命名空间的
# 实际 fts/articles 比值" —— 写死数字的测试明天就会假失败。


def _counts(db_path: Path) -> tuple[int, int]:
    con = store.read_only_connect(db_path)
    try:
        articles = int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        fts = int(con.execute("SELECT COUNT(*) FROM articles_fts").fetchone()[0])
    finally:
        con.close()
    return articles, fts


def _copied_db(isolated_data_root: Path, category: str) -> Path:
    db_path = isolated_data_root / "01_categories" / category / "articles.db"
    assert db_path.exists(), f"隔离副本里没有 {category} 库:{db_path}"
    return db_path


def _add_unindexed_article(db_path: Path, tag: str) -> None:
    """模拟"采集入库了但没跑 analyze"的真实状态:只写 articles,不写 articles_fts。"""
    url = f"https://www.toutiao.com/article/p25-{tag}/"
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO articles(source, category, title, url, url_normalized, content,"
            " fetched_at, content_hash)"
            " VALUES('toutiao_tech', 'technology', ?, ?, ?, ?, '2026-11-16T02:25:57+08:00', ?)",
            (f"P25 未索引标题 {tag}", url, normalize_url(url),
             "开源效率工具与人工智能的深度评测正文。" * 6, f"p25-lag-{tag}"),
        )
        con.commit()
    finally:
        con.close()


def _fts_line(capsys) -> str:
    assert doctor.run_doctor() in (0, 1)
    lines = [line for line in capsys.readouterr().out.splitlines() if "db.fts_coverage" in line]
    assert lines, "体检输出里必须有 db.fts_coverage 这一行"
    return lines[-1]


def test_doctor_warns_on_fts_lag(isolated_data_root: Path, capsys):
    """文章比索引多(11-16 凌晨 job 54/70 的真实状态)必须 WARN,并给能跑通的补跑命令。"""
    db_path = _copied_db(isolated_data_root, "technology")
    articles, fts = _counts(db_path)
    if fts >= articles:
        _add_unindexed_article(db_path, "doctor-lag")
        articles, fts = _counts(db_path)
    assert fts < articles, f"造不出滞后状态(articles={articles} fts={fts}),测试前提不成立"
    line = _fts_line(capsys)
    assert "WARN" in line, f"索引滞后必须 WARN,实测:{line}"
    assert f"technology {fts}/{articles}" in line, f"WARN 里要点名滞后命名空间和实际比值,实测:{line}"
    assert "ttxt analyze" in line, "必须给补跑命令(索引只在 analyze → rag.rebuild_index 里重建)"


def test_rag_rebuild_closes_the_gap(isolated_data_root: Path, capsys):
    """补跑命令必须真管用:重建之后索引行数等于文章行数,该命名空间不再出现在滞后名单里。"""
    db_path = _copied_db(isolated_data_root, "technology")
    _add_unindexed_article(db_path, "rag-rebuild")
    before_articles, before_fts = _counts(db_path)
    assert before_fts < before_articles
    stats = rag.rebuild_index("technology", db_path=db_path)
    after_articles, after_fts = _counts(db_path)
    assert after_fts == after_articles, f"rebuild_index 必须补齐索引,实测 {stats}"
    line = _fts_line(capsys)
    assert f"technology {after_fts}/{after_articles}" not in line, (
        f"重建后该命名空间不得再出现在滞后名单:{line}"
    )
