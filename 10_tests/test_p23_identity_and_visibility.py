"""P23:同一 URL 同一标题的"正文抖动"不得再谎报成身份塌陷;被拒插入必须进体检。

生产证据(取证脚本与输出在 ``07_tmp/r26_identity_conflict/``,2026-11-15):

1. ``09_logs/ttxt-20261115.jsonl:59-63`` —— 15:53:09,``chinanews_society``/``social``
   三条 ``insert.url_identity_conflict``,外加 ``insert.url_identity_summary conflicts=3``
   与 ``crawl.url_identity_conflict conflicts=3``。
2. ``probe_conflict_out.txt`` —— 这三个 URL 在库里就是 id 13/16/20,标题与日志里的
   **逐字相同**,``completeness='full'``,正文 708/846/684 字,无同标题、同哈希的其他行。
3. ``probe_item_pages_out.txt``(21:23 只读复现)—— 三页都是 HTTP 200,
   配置的 ``div.left_zw`` 选择器又取到 **708/846/684 字**(与库里长度完全一致),
   ``h1`` 标题也一致;第二个选择器 ``div.content_maincontent_content`` 多 14 字
   ⇒ 正文哈希跨轮不稳定。
4. ``probe_job_meta_out.txt`` —— job 66 ``items_enriched=20``、``item_page_errors=0``:
   15:53 那三条**确实抓到了文章页正文**,不是摘要级退化。

结论:同一 URL、同一标题、长度相同而字节不同 = 同一篇文章被重新抓了一次。
P20 只放行"正文更长"的升级,于是"长度不增的哈希抖动"全落进 ``url_identity_conflict``,
注册类目每天制造假警报,真正的身份塌陷反而被噪声埋掉。本文件锁住修好的口径:

- 同一标题 ⇒ 同一身份:正文不比已存长就**保留原行**,计 ``bodies_kept_existing``;
- ``url_identity_conflict`` 只留给**标题不同**(或同批内同 URL 两次正文不同)的塌陷;
- 被拒插入与判定事件必须被 ``ttxt doctor`` 按 24h 窗口数出来(``crawler.insert_judgements``),
  否则"保护拦下了 3 条"和"根本没发生"在体检里长得一样(体检 2026-11-15 §4.5 P1)。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ttxt import config as cfg
from ttxt import doctor, housekeep, logging as ttxt_logging
from ttxt.crawler import extract, store

CATEGORY = "p23cat"
ARTICLE_URL = "https://www.chinanews.com.cn/sh/2026/11-14/10732158.shtml"
TITLE = "串联陆游、鲁迅等名人古迹 浙江绍兴循图Citywalk引游人"


def _item(title: str, content: str, url: str = ARTICLE_URL) -> extract.ExtractedItem:
    return extract.ExtractedItem(
        title=title,
        content=content,
        excerpt=content[:40],
        author=None,
        published_at=None,
        url=url,
        url_is_page_fallback=False,
    )


def _rows(db_path: Path) -> list:
    import sqlite3

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute("SELECT * FROM articles ORDER BY id")]
    finally:
        con.close()


def _log_events() -> list[dict]:
    path = ttxt_logging.log_path()
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# ------------------------------------------- 1 同一标题 + 哈希抖动:保留原行,不算塌陷


def test_same_title_equal_length_hash_drift_keeps_existing_row(tmp_path):
    """生产原样:同一 URL、同一标题、正文**等长但字节不同** ⇒ 保留原行,不算身份塌陷。

    修复前:``{"inserted": 0, "duplicates": 1, "url_identity_conflict": 1}``
    —— 实测 job 66 就是这样对三篇好文章喊了三次假警报。
    """
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "p23.db")
    stored_body = "绍兴Citywalk" * 59 + "甲"  # 708 字,completeness=full
    first = store.insert_items(CATEGORY, "chinanews_society", [_item(TITLE, stored_body)], db_path=path)
    assert first == {"inserted": 1, "duplicates": 0}
    before = _rows(path)[0]
    assert before["completeness"] == "full"

    drifted_body = "绍兴Citywalk" * 59 + "乙"  # 与 stored_body 等长、哈希不同
    assert len(drifted_body) == len(stored_body)
    second = store.insert_items(CATEGORY, "chinanews_society", [_item(TITLE, drifted_body)], db_path=path)
    assert second == {"inserted": 0, "duplicates": 1, "bodies_kept_existing": 1}, (
        "同一标题的重复抓取不得再计 url_identity_conflict;要计 bodies_kept_existing"
    )
    assert "url_identity_conflict" not in second, "身份塌陷这个词只留给标题不同的情况"

    rows = _rows(path)
    assert len(rows) == 1, "同一身份只允许一行(没变)"
    after = rows[0]
    assert after["id"] == before["id"] and after["content"] == stored_body
    assert after["content_hash"] == before["content_hash"], "不许为了对齐哈希改写原行"
    assert after["fetched_at"] == before["fetched_at"] and after["completeness"] == "full"


def test_same_title_shorter_body_keeps_existing_row(tmp_path):
    """同一标题、正文更短(文章页退化成摘要)⇒ 同样保留原行,不把语料质量改差。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "p23.db")
    full_body = "中新网完整正文" * 80  # 560 字 ⇒ full
    assert store.insert_items(CATEGORY, "chinanews_society", [_item(TITLE, full_body)], db_path=path) == {
        "inserted": 1,
        "duplicates": 0,
    }
    counts = store.insert_items(CATEGORY, "chinanews_society", [_item(TITLE, "串联陆游等古迹引游人")], db_path=path)
    assert counts == {"inserted": 0, "duplicates": 1, "bodies_kept_existing": 1}
    assert _rows(path)[0]["content"] == full_body, "短正文不得覆盖已存的完整正文(C-7 正文最长者胜)"
    assert store.count_articles(CATEGORY, db_path=path) == 1


def test_identical_refetch_stays_plain_duplicate(tmp_path):
    """字节完全一致的真重复仍然是纯重复:不得新增任何计数键。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "p23.db")
    body = "同一篇文章同一正文" * 30
    assert store.insert_items(CATEGORY, "chinanews_society", [_item(TITLE, body)], db_path=path) == {
        "inserted": 1,
        "duplicates": 0,
    }
    again = store.insert_items(CATEGORY, "chinanews_society", [_item(TITLE, body)], db_path=path)
    assert again == {"inserted": 0, "duplicates": 1}, "真重复不得带出 bodies_kept_existing"


def test_different_title_same_url_is_still_identity_conflict(tmp_path):
    """标题不同才是身份塌陷:这个词必须留给真正塞错文章的情况(C-7b/demo 30 条教训)。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "p23.db")
    body = "甲文章的正文内容" * 30
    assert store.insert_items(CATEGORY, "src", [_item("甲标题", body)], db_path=path) == {
        "inserted": 1,
        "duplicates": 0,
    }
    counts = store.insert_items(
        CATEGORY, "src", [_item("乙标题", "乙文章的正文内容" * 30)], db_path=path
    )
    assert counts == {"inserted": 0, "duplicates": 1, "url_identity_conflict": 1}
    assert "bodies_kept_existing" not in counts
    assert _rows(path)[0]["title"] == "甲标题", "绝不悄悄改写别人的标题"


def test_body_kept_event_is_logged(tmp_path):
    """保留原行不能静默:必须留下 ``insert.body_kept_existing``,带 article_id 与两侧字数。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "p23.db")
    stored = "长正文" * 200
    store.insert_items(CATEGORY, "chinanews_society", [_item(TITLE, stored)], db_path=path)
    article_id = _rows(path)[0]["id"]
    store.insert_items(CATEGORY, "chinanews_society", [_item(TITLE, "短" * len(stored))], db_path=path)
    events = [event for event in _log_events() if event.get("event") == "insert.body_kept_existing"]
    assert events, "保留原行必须可追溯到逐条日志"
    meta = events[-1]["meta"]
    assert meta["article_id"] == article_id
    assert meta["old_chars"] == len(stored) and meta["new_chars"] == len(stored)
    assert meta["category"] == CATEGORY and meta["source"] == "chinanews_society"


# ----------------------------------------- 2 体检:被拒插入与判定事件必须有 24h 计数


def _write_log(lines: list[dict], log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "ttxt-20261115.jsonl").open("w", encoding="utf-8") as fh:
        for record in lines:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_insert_judgement_rows_warns_with_counts(tmp_path, monkeypatch):
    """身份塌陷非零 ⇒ WARN,并把来源与判定事件数量当场列出(修的是抽取器,不是删数据)。"""
    now = datetime.fromisoformat("2026-11-15T21:00:00+08:00")
    recent = (now - timedelta(minutes=5)).isoformat()
    _write_log(
        [
            {"ts": recent, "level": "warn", "module": "store", "event": "insert.url_identity_conflict",
             "meta": {"category": "social", "source": "chinanews_society", "url": ARTICLE_URL}},
            {"ts": recent, "level": "warn", "module": "store", "event": "insert.url_identity_conflict",
             "meta": {"category": "social", "source": "chinanews_society", "url": "u2"}},
            {"ts": recent, "level": "warn", "module": "store", "event": "insert.page_url_collapse",
             "meta": {"category": "demo", "source": "quotes", "url": "u3"}},
            {"ts": recent, "level": "info", "module": "store", "event": "insert.body_kept_existing",
             "meta": {"category": "social", "source": "chinanews_society", "article_id": 13}},
            {"ts": recent, "level": "info", "module": "store", "event": "insert.body_upgraded",
             "meta": {"category": "social", "source": "chinanews_society", "article_id": 13}},
        ],
        tmp_path / "logs",
    )
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path / "logs")
    level, name, detail = doctor.insert_judgement_rows(window_hours=24, now=now)[0]
    assert level == "WARN" and name == "crawler.insert_judgements"
    assert "身份塌陷 2 条" in detail and "来源页 URL 塌陷 1 条" in detail
    assert "保留原行 1 条" in detail and "正文升级 1 条" in detail
    assert "social/chinanews_society×2" in detail, "塌陷集中在哪个类目/来源必须直接可见"


def test_insert_judgement_rows_pass_and_window_and_bad_lines(tmp_path, monkeypatch):
    """没有塌陷 ⇒ PASS;窗口外的事件不得计数;读不动的日志与坏行要喊出来,不得静默。"""
    now = datetime.fromisoformat("2026-11-15T21:00:00+08:00")
    old = (now - timedelta(hours=30)).isoformat()
    recent = (now - timedelta(hours=1)).isoformat()
    log_dir = tmp_path / "logs"
    _write_log(
        [
            {"ts": old, "level": "warn", "module": "store", "event": "insert.url_identity_conflict",
             "meta": {"category": "social", "source": "s"}},
            {"ts": recent, "level": "info", "module": "store", "event": "insert.body_upgraded",
             "meta": {"category": "social", "source": "s", "article_id": 1}},
        ],
        log_dir,
    )
    with (log_dir / "ttxt-20261115.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{ 这不是 JSON\n")
    monkeypatch.setattr(cfg, "LOG_DIR", log_dir)
    level, _name, detail = doctor.insert_judgement_rows(window_hours=24, now=now)[0]
    assert level == "WARN", "坏行不得被静默跳过"
    assert "身份塌陷 0 条" in detail and "正文升级 1 条" in detail
    assert "坏行 1 条已跳过" in detail

    (log_dir / "ttxt-20261115.jsonl").write_text(
        json.dumps({"ts": recent, "level": "info", "module": "store", "event": "insert.body_upgraded",
                    "meta": {"category": "social", "source": "s", "article_id": 1}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    level, _name, detail = doctor.insert_judgement_rows(window_hours=24, now=now)[0]
    assert level == "PASS" and "身份塌陷 0 条" in detail and "正文升级 1 条" in detail


# ------------------------------------------------ 3 磁盘口径:根目录实测必须进得了预算表


def _fake_major_paths(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """把磁盘桶换成 tmp 小目录,避免每次单测都真扫 11 GB(桶的语义不变)。"""
    buckets = {
        "control": tmp_path / "00_control",
        "categories": tmp_path / "01_categories",
        "crawler": tmp_path / "02_crawler",
        "intelligence": tmp_path / "03_intelligence",
        "writing": tmp_path / "04_writing",
        "dashboard": tmp_path / "05_dashboard",
        "agents": tmp_path / "06_agents",
        "rules": tmp_path / "07_rules",
        "wiki": tmp_path / "08_wiki",
        "logs": tmp_path / "09_logs",
        "tests": tmp_path / "10_tests",
        "backups": tmp_path / "11_backups",
        "temp": tmp_path / "99_temp",
        "scratch": tmp_path / "07_tmp",
        "assets": tmp_path / "资产库",
        "venv": tmp_path / ".venv",
        "pip_cache": tmp_path / ".pip-cache",
        "tools": tmp_path / "tools",
        "git": tmp_path / ".git",
        "secrets": tmp_path / ".secrets",
    }
    monkeypatch.setattr(housekeep, "MAJOR_PATHS", buckets)
    return buckets


def test_housekeep_lists_unregistered_root_dirs(tmp_path, monkeypatch):
    """白名单桶是这次的盲区:根目录里没进桶的一级目录必须按大小列出来(只报告,不删除)。"""
    root = tmp_path / "root"
    buckets = _fake_major_paths(monkeypatch, root)
    for path in buckets.values():
        path.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("x", encoding="utf-8")
    stray = root / "没登记的大目录"
    (stray / "子目录").mkdir(parents=True)
    (stray / "子目录" / "blob.bin").write_bytes(b"z" * 4096)
    (stray / "small.bin").write_bytes(b"z" * 100)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", root)
    report = housekeep.report()
    names = {item["name"]: item["bytes"] for item in report["unaccounted_dirs"]}
    assert "没登记的大目录" in names
    assert names["没登记的大目录"] == 4196
    assert report["unaccounted_bytes"] >= 4196
    assert report["root_total_bytes"] >= report["unaccounted_bytes"]
    assert report["root_scan_error"] is None


def test_housekeep_scratch_bucket_is_measured_against_its_own_cap(tmp_path, monkeypatch):
    """07_tmp 必须单独计量:以前只有 99_temp 有额,而真正在涨的是 07_tmp(实测 0.753 GB)。"""
    root = tmp_path / "root"
    buckets = _fake_major_paths(monkeypatch, root)
    for path in buckets.values():
        path.mkdir(parents=True, exist_ok=True)
    (buckets["scratch"] / "evidence.json").write_bytes(b"q" * 5000)
    (buckets["temp"] / "old.bin").write_bytes(b"q" * 10)
    monkeypatch.setattr(cfg, "PROJECT_ROOT", root)
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: {
            "version": 1,
            "disk": {"project_budget_gb": 20, "min_free_gb": 5, "temp_max_gb": 2, "scratch_max_gb": 0.000001},
        },
    )
    report = housekeep.report()
    assert "scratch" in report["sizes_bytes"] and "assets" in report["sizes_bytes"]
    status = report["caps_status"]
    assert status["scratch_bytes"] == 5000 and status["scratch_within"] is False
    assert status["temp_bytes"] == 10 and status["temp_within"] is True
    assert report["caps"]["scratch_max_gb"] == 0.000001
    assert report["root_total_gb"] >= report["project_total_gb"]


def test_scratch_dir_config_points_at_real_scratch_root():
    """口径必须对上现实:规则 25/32 与全部取证脚本写的是 ``07_tmp``,不是 ``99_temp``。"""
    assert cfg.SCRATCH_DIR == cfg.PROJECT_ROOT / "07_tmp"
    config = cfg.load_config()
    assert config["paths"]["scratch"] == "07_tmp"
    assert float(config["disk"]["scratch_max_gb"]) > 0
    assert housekeep.MAJOR_PATHS["scratch"] == cfg.SCRATCH_DIR


# ------------------------------------------------------------ 4 体检输出里要看得见


def test_run_doctor_prints_new_checks(isolated_data_root, monkeypatch, capsys):
    """``ttxt doctor`` 必须真把这三项打出来,否则整改完还是没人能验证。"""
    assert doctor.run_doctor() in (0, 1)
    joined = capsys.readouterr().out
    assert "crawler.insert_judgements" in joined
    assert "disk.root_budget" in joined
    assert "disk.scratch" in joined
    assert "disk.accounting" in joined
