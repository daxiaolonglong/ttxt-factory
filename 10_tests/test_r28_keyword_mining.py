# -*- coding: utf-8 -*-
"""R28:关键词发现(闭环缺的另一半)。

实测缺口(2026-11-18):``keywords`` 表只有 seed 来源,没有任何从语料长新词的代码
⇒ ``auto_feedback`` 只能为固定几个种子词反复入队,系统不可能"越来越聪明"。

护栏口径(本轮定死):
- **类目未标定就不挖**(阈值还是设计假定,据此挖词等于把假说当事实);
- 只从标题切 2-4 字中文 n-gram,跨标点不拼;
- 至少出现在 ``min_docs`` 篇不同爆款里,且 lift ≥ ``min_lift``;
- 五道闸门:①首尾虚词;②出现次数级延长判定;③左右邻字熵 ≥ 0.5;④类目特异性(只读其他类目标题);
  ⑤词性(纯副词/时间/数词/动词等句子成分不是话题;首尾切出单字说明跨了词边界);
- 与现有词互为子串的一律丢弃(不制造近重复);停用词与纯数词丢弃;
- 只插入 origin='mined',不改已有词、不入队;重跑幂等(第二次 inserted 0);旧挖词退场并自愈孤儿评分行。

真实语料验证(technology,369 篇标题):过滤前首轮 10 个词里 6 个是碎片
(的地/国这/何中/为何中…);五道闸门之后剩 5 个都真是话题(降价/航天/京东/航天员/神舟二十),
上一版算法留下的 5 个句子成分(中国科/亿美元/已有/年后/最后)同时在退场里被清掉。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ttxt.analysis import keywords, viral
from ttxt.crawler import store

# 爆款标题:同一个话题词出现在**不同上下文**里(纯模板标题邻字熵为 0,会被闸门如实拒掉)
GLUCOSE_TITLES = [
    "家用血糖仪选购指南",
    "血糖仪测不准怎么办",
    "老人血糖仪推荐这三款",
    "跑步前后用血糖仪看数据",
    "血糖仪价格差在哪",
    "医院同款血糖仪测评",
]
INSULIN_TITLES = [
    "医生说的胰岛素用法",
    "打胰岛素会不会上瘾",
    "胰岛素泵和针哪个好",
    "胰岛素降价了吗",
    "胰岛素保存要注意什么",
    "长期用胰岛素的代价",
]
CHIP_TITLES = [
    "聊聊国产芯片的现状",
    "国产芯片能否弯道超车",
    "这家国产芯片公司上市了",
    "国产芯片价格战开打",
    "国产芯片出货量创新高",
    "看国产芯片的短板在哪",
]


def _make_db(tmp_path: Path, titles: list[str], seeded: list[str] | None = None) -> Path:
    db_path = tmp_path / "demo.db"
    store.migrate_articles_db("demo", db_path=db_path)
    now = "2026-11-18T21:00:00+08:00"
    con = sqlite3.connect(db_path)
    try:
        for index, title in enumerate(titles):
            text = title + "。" + "正文内容用于满足清洗长度要求。" * 5
            con.execute(
                "INSERT INTO articles(category, source, title, content, cleaned_content, text_length,"
                " url, url_normalized, content_hash, fetched_at) VALUES('demo', 'src', ?, ?, ?, ?, ?, ?, ?, ?)",
                (title, text, text, len(text), f"https://x.test/{index}",
                 f"https://x.test/{index}", f"h{index}", now),
            )
        for word in seeded or []:
            con.execute(
                "INSERT INTO keywords(keyword, category, origin, status, created_at)"
                " VALUES(?, 'demo', 'seed', 'active', ?)",
                (word, now),
            )
        con.commit()
    finally:
        con.close()
    return db_path


def _mark_viral(db_path: Path, article_ids: list[int]) -> None:
    con = sqlite3.connect(db_path)
    try:
        for article_id in article_ids:
            con.execute(
                "INSERT OR REPLACE INTO viral_labels(article_id, label, confidence_tier, score, labeled_at)"
                " VALUES(?, 'potential', 'measured', 0.2, '2026-11-18T21:00:00+08:00')",
                (article_id,),
            )
        con.commit()
    finally:
        con.close()


def _calibrate(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO articles_meta(key, value) VALUES(?, ?)",
            (viral.CALIBRATION_META_KEY, json.dumps({"thresholds": {"verified_viral": 0.1,
                                                                  "potential": 0.05, "normal": 0.01}})),
        )
        con.commit()
    finally:
        con.close()


def _keyword_rows(db_path: Path) -> list[tuple[str, str]]:
    con = sqlite3.connect(db_path)
    try:
        return [(row[0], row[1]) for row in con.execute("SELECT keyword, origin FROM keywords ORDER BY id")]
    finally:
        con.close()


def _fillers(n: int) -> list[str]:
    return [f"普通文章标题{i}" for i in range(n)]


def test_uncalibrated_category_mines_nothing(tmp_path):
    """类目没标定 ⇒ 一个词都不许挖(阈值是假定,不能当事实)。"""
    titles = GLUCOSE_TITLES + _fillers(10)
    db_path = _make_db(tmp_path, titles)
    _mark_viral(db_path, list(range(1, 7)))

    stats = keywords.mine_keywords("demo", db_path=db_path, background=[])

    assert stats["status"] == "skipped_uncalibrated"
    assert stats["inserted"] == 0
    assert _keyword_rows(db_path) == []


def test_mines_repeated_viral_term(tmp_path):
    """爆款标题里反复出现的真话题要挖出来,并记清打分依据。"""
    titles = GLUCOSE_TITLES + _fillers(24)
    db_path = _make_db(tmp_path, titles)
    _mark_viral(db_path, list(range(1, 7)))
    _calibrate(db_path)

    stats = keywords.mine_keywords("demo", db_path=db_path, top_n=50, background=[])

    assert stats["status"] == "ok"
    assert stats["viral_docs"] == 6
    assert stats["corpus_titles"] == 30
    mined = [item["keyword"] for item in stats["top"]]
    assert "血糖仪" in mined
    detail = next(item for item in stats["top"] if item["keyword"] == "血糖仪")
    assert detail["viral_docs"] == 6
    assert detail["lift"] == 5.0          # 爆款里 6/6,全库 6/30
    assert detail["left_entropy"] >= keywords._ENTROPY_MIN
    assert detail["right_entropy"] >= keywords._ENTROPY_MIN
    assert stats["inserted"] == len(stats["top"]) > 0

    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM keywords WHERE origin='mined'").fetchone()[0] == stats["inserted"]
    finally:
        con.close()


def test_drops_near_duplicate_of_existing_keyword(tmp_path):
    """与现有词互为子串的候选一律丢弃(不制造近重复关键词),其余候选照常产出。"""
    titles = GLUCOSE_TITLES + INSULIN_TITLES + _fillers(24)
    db_path = _make_db(tmp_path, titles, seeded=["血糖"])
    _mark_viral(db_path, list(range(1, 13)))
    _calibrate(db_path)

    stats = keywords.mine_keywords("demo", db_path=db_path, top_n=50, background=[])

    assert stats["status"] == "ok"
    mined_words = [word for word, origin in _keyword_rows(db_path) if origin == keywords.MINE_ORIGIN]
    assert "胰岛素" in mined_words, "与「血糖」无关的话题词应当照常挖出"
    assert all("血糖" not in word for word in mined_words)


def test_fragment_that_only_extends_into_a_longer_word_is_rejected(tmp_path):
    """碎片闸门:每次出现都被同一个字延长成更长的词(产芯片 → 国产芯片)时不许单独成词。"""
    titles = CHIP_TITLES + _fillers(24)
    db_path = _make_db(tmp_path, titles)
    _mark_viral(db_path, list(range(1, 7)))
    _calibrate(db_path)

    stats = keywords.mine_keywords("demo", db_path=db_path, top_n=50, background=[])

    mined = {item["keyword"] for item in stats["top"]}
    assert "国产芯片" in mined
    assert "产芯片" not in mined, "「产芯片」的每一次出现都来自「国产芯片」,不是独立的词"


def test_templated_titles_with_frozen_context_mine_nothing(tmp_path):
    """纯模板标题(话题词左右邻字永远不变)熵为 0 ⇒ 不挖:模板不是话题。"""
    titles = [f"血糖仪推荐第{i}篇" for i in range(4, 10)] + _fillers(24)
    db_path = _make_db(tmp_path, titles)
    _mark_viral(db_path, list(range(1, 7)))
    _calibrate(db_path)

    stats = keywords.mine_keywords("demo", db_path=db_path, top_n=50, background=[])

    assert stats["status"] == "ok"
    assert stats["inserted"] == 0
    assert not [item for item in stats["top"] if item["keyword"] == "血糖仪"]


def test_second_run_is_idempotent(tmp_path):
    """挖词只插入,第二次跑不许重复插入,也不许改动已有词。"""
    titles = GLUCOSE_TITLES + _fillers(24)
    db_path = _make_db(tmp_path, titles)
    _mark_viral(db_path, list(range(1, 7)))
    _calibrate(db_path)

    first = keywords.mine_keywords("demo", db_path=db_path, background=[])
    second = keywords.mine_keywords("demo", db_path=db_path, background=[])

    assert first["inserted"] > 0
    assert second["inserted"] == 0
    assert len(_keyword_rows(db_path)) == first["inserted"]


def test_stale_mined_keywords_are_pruned(tmp_path):
    """旧轮次挖出、这一轮不再产出的词要退场(连同 performance 行),种子词一个字都不许动。"""
    titles = GLUCOSE_TITLES + _fillers(24)
    db_path = _make_db(tmp_path, titles, seeded=["人工智能"])
    _mark_viral(db_path, list(range(1, 7)))
    _calibrate(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO keywords(keyword, category, origin, status, created_at)"
            " VALUES('过期话题', 'demo', 'mined', 'active', '2026-11-17T00:00:00+08:00')"
        )
        stale_id = con.execute("SELECT id FROM keywords WHERE keyword='过期话题'").fetchone()[0]
        con.execute(
            "INSERT INTO keyword_performance(keyword_id, collected, quality_articles, updated_at)"
            " VALUES(?, 0, 0, '2026-11-17T00:00:00+08:00')",
            (stale_id,),
        )
        con.commit()
    finally:
        con.close()

    stats = keywords.mine_keywords("demo", db_path=db_path, top_n=50, background=[])

    assert stats["pruned"] == 1
    words = [word for word, _ in _keyword_rows(db_path)]
    assert "过期话题" not in words
    assert "人工智能" in words, "种子词不属于挖词生命周期,不许被清"
    con = sqlite3.connect(db_path)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM keyword_performance WHERE keyword_id=?", (stale_id,)
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_same_title_repeated_is_not_multiple_evidence(tmp_path):
    """同一标题被多篇文章重复搬用(连带爆款标签一起复制)只算一次编辑选择,不是多次证据。

    实测(2026-11-18 social):「物理AI进入规模化验证期…」被 4 篇重复文章带着同一标签,
    让「入规/成资」这种跨词边界碎片凑够 min_docs=3,被当成话题挖出来。
    """
    duplicated = ["物理AI进入规模化验证期"] * 4
    others = ["血糖仪选购要点", "跑步用血糖仪看数据"]
    db_path = _make_db(tmp_path, duplicated + others + _fillers(24))
    _mark_viral(db_path, list(range(1, 7)))    # 6 篇爆款,但其中 4 篇是同一标题
    _calibrate(db_path)

    stats = keywords.mine_keywords("demo", db_path=db_path, background=[])

    assert stats["status"] == "ok"
    assert stats["viral_docs"] == 3, "4 篇同标题只算 1 个爆款标题 ⇒ 3 个不同标题"
    assert stats["duplicate_titles_dropped"] == 3
    mined = {item["keyword"] for item in stats["top"]}
    assert "入规" not in mined, "碎片只来自那一个重复标题,不是多个爆款共同的话题"


def test_too_common_word_is_not_mined_in_large_corpus(tmp_path, monkeypatch):
    """大语料里到处都出现的词(企业)是通用词,不是话题;小语料不启用这道闸门。"""
    monkeypatch.setattr(keywords, "MINE_MAX_PREVALENCE_MIN_TITLES", 10)
    monkeypatch.setattr(keywords, "MINE_MAX_PREVALENCE", 0.3)
    titles = GLUCOSE_TITLES + [f"企业转型第{i}个案例" for i in range(20)] + _fillers(4)
    db_path = _make_db(tmp_path, titles)
    _mark_viral(db_path, list(range(1, 13)))
    _calibrate(db_path)

    stats = keywords.mine_keywords("demo", db_path=db_path, top_n=50, background=[])

    mined = {item["keyword"] for item in stats["top"]}
    assert "血糖仪" in mined
    assert "企业" not in mined
    assert stats["max_prevalence"] == 0.3


def test_orphan_performance_rows_are_cleaned(tmp_path):
    """指向已删除关键词的评分行(历史清理留下的)要自愈清掉,否则词数永远对不上。"""
    titles = GLUCOSE_TITLES + _fillers(24)
    db_path = _make_db(tmp_path, titles)
    _mark_viral(db_path, list(range(1, 7)))
    _calibrate(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO keyword_performance(keyword_id, collected, quality_articles, updated_at)"
            " VALUES(999999, 3, 3, '2026-11-17T00:00:00+08:00')"
        )
        con.commit()
    finally:
        con.close()

    keywords.mine_keywords("demo", db_path=db_path, background=[])

    con = sqlite3.connect(db_path)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM keyword_performance p LEFT JOIN keywords k ON k.id=p.keyword_id"
            " WHERE k.id IS NULL"
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_stopwords_and_plain_words_are_not_mined(tmp_path):
    """标题里的套话(记者/网友/震惊/真相…)不许变成关键词,同批里的真话题照常产出。"""
    titles = GLUCOSE_TITLES + [f"记者发现网友震惊的{i}个真相" for i in range(8)] + _fillers(20)
    db_path = _make_db(tmp_path, titles)
    _mark_viral(db_path, list(range(1, 15)))
    _calibrate(db_path)

    stats = keywords.mine_keywords("demo", db_path=db_path, top_n=50, background=[])

    mined = {item["keyword"] for item in stats["top"]}
    assert "血糖仪" in mined
    assert not mined & set(keywords.STOPWORDS)
    for word in ("记者", "网友", "震惊", "真相"):
        assert word not in mined


def test_pure_numerals_are_not_mined(tmp_path):
    """纯数词串(二十/三百)没有话题含义,不许进关键词表。"""
    titles = [
        "发布会定在二十号",
        "二十年前的老设备",
        "第二十届大会开幕",
        "二十个常见错误",
        "血糖仪选二十款对比",
        "预算二十万能买什么",
    ] + _fillers(24)
    db_path = _make_db(tmp_path, titles)
    _mark_viral(db_path, list(range(1, 7)))
    _calibrate(db_path)

    stats = keywords.mine_keywords("demo", db_path=db_path, top_n=50, background=[])

    mined = {item["keyword"] for item in stats["top"]}
    assert not any(all(char in keywords._NUMERAL_CHARS for char in word) for word in mined)


def test_generic_word_common_in_every_category_is_not_mined(tmp_path):
    """类目特异性闸门:每个类目标题里都一样常见的词(手机)不是话题,只在单类目密集的词才是。

    用「手机」而不是「最后」:后者是副词,会被第五道词性闸门先拒掉,那就证明不了本闸门有效。
    """
    titles = [
        "手机上看血糖仪怎么选",
        "他说用手机连血糖仪",
        "手机血糖仪配对方法",
        "血糖仪和手机哪个准",
        "我手机上查血糖仪价格",
        "手机里存血糖仪记录",
    ] + _fillers(24)
    db_path = _make_db(tmp_path, titles)
    _mark_viral(db_path, list(range(1, 7)))
    _calibrate(db_path)
    # 其他类目的标题里同样到处是「手机」:这类词进了关键词只会污染采集过滤
    other_one = [f"手机拍照第{i}个技巧" for i in range(20)]
    other_two = [f"手机电池续航第{i}天" for i in range(20)]

    stats = keywords.mine_keywords(
        "demo", db_path=db_path, top_n=50, background=[("other1", other_one), ("other2", other_two)]
    )

    mined = {item["keyword"] for item in stats["top"]}
    assert "血糖仪" in mined
    assert "手机" not in mined
    assert stats["background_categories"] == 2

