# -*- coding: utf-8 -*-
"""P41(R64-1):写作素材必须**真的在讲这个主题**,离题候选不进池、不计密度、不占素材槽。

实测根因(2026-11-21,10 题生产快照探针 ``07_tmp/r64_material_probe.json``):
- BM25 只在候选池内**相对**归一(``norm = (worst - bm25)/(worst - best)``),没有绝对下限;
- ``rag.search`` 在 AND 空结果且取词 >3 时回退 OR,只沾一个双字词的稿子也会被塞进候选;
- 结果 10 题里 4 题(邻里纠纷/高空抛物/装修 噪音/催婚)的 5 个素材槽被离题稿占满:
  「装修 噪音」选中 外卖副业/棋牌室/KTV 加盟/旧衣服回收/北漂租房,「高空抛物」选中
  法律常识合集/AI民生场景/恒大AI。模型拿不相干材料只会编造数字与引用 ⇒ 每篇白烧约 4.5 分钟。

钉死的口径:
- 相关度尺子是 ``rag.topic_relevance``:与 FTS **同一分词器**(双字词)、非重叠计数,
  ``title_hits>=1 且 per_1k>=2.0`` 或 ``per_1k>=8.0`` 才算相关;阈值来自上述探针
  (6 个健康主题 7—15 篇相关 vs 4 个漂移主题 0—1 篇相关,无中间地带);
- 离题候选**不进池**:``candidates / full_candidates / distinct_sources / selected``
  只数相关候选 ⇒ 既有两个取材门槛(min_full/min_sources)自动变严,**不新增也不放宽任何阈值**;
- 剔除多少、剔掉的是谁(标题/密度/完成度)必须如实写进 ``material_stats["relevance"]``;
- 取词为空(纯英文/符号主题)⇒ 无尺子可用,如实跳过过滤,不假装拦下。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ttxt.analysis import rag
from ttxt.crawler import cleaner, store
from ttxt.writing import precheck, workflow

MIX_TOPIC = "装修 噪音"
FALLBACK_TOPIC = "高空抛物 赔偿 标准"
NOW = "2026-11-21T09:00:00+08:00"

# 相关稿:标题点题 + 正文密集(两条件同时满足)
ON_TITLE = "小区装修噪音扰民,谁来处理?"
ON_BODY = (
    "装修噪音扰民的处理办法:先向物业报备装修时段,噪音超标可投诉,居委会与城管介入协调。" * 10
    + "装修时段外的噪音记录可作证据,投诉要写清时段与次数。"
)

# 离题稿:FTS 命中(两个词都出现过一次)但通篇在讲别的 ⇒ 密度不达标就被剔
OFF_TITLE = "外卖副业实操:先算成本再排班"
OFF_BODY = (
    "跑外卖副业的实操记录:先算清车辆与装备成本,再按高峰排班,最后核算真实时薪与平台补贴。" * 14
    + "顺带提一句:房东家装修时有点噪音,不影响接单安排。2024年5月接单量 1200 单。"
)

# 离题稿(OR 回退现场):正文只沾 "标准/赔偿",标题也不点题
FALLBACK_TITLE = "副业收益怎么算"
FALLBACK_BODY = (
    "跑外卖副业的实操记录:先算清车辆与装备成本,再按高峰排班,最后核算真实时薪与平台补贴。" * 14
    + "顺带提一句:行业收益标准各家不同,赔偿规则也要看合同。2024年5月接单量 1200 单。"
)
# 相关稿(满足 AND 全部取词)
FALLBACK_ON_TITLE = "高空抛物赔偿标准与责任划分"
FALLBACK_ON_BODY = (
    "高空抛物致人损害时,赔偿标准按医疗费与误工损失计算;找不到具体侵权人时,可能加害的建筑物使用人补偿。" * 9
    + "高空抛物的赔偿标准以票据为准,物业未尽安全保障责任也要赔。"
)


def _insert(con, category: str, marker: str, host: str, title: str, text: str,
            author: str | None = None) -> int:
    url = f"https://{host}/a-{marker}"
    cur = con.execute(
        "INSERT INTO articles(category, source, title, content, cleaned_content, text_length,"
        " completeness, author, url, url_normalized, content_hash, fetched_at)"
        " VALUES(?, 'src', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            category, title, text, text,
            cleaner.usable_length(text), cleaner.completeness_of(text), author,
            url, url, f"hash-{category}-{marker}", NOW,
        ),
    )
    return int(cur.lastrowid)


def _corpus(tmp_path: Path, category: str, pairs: list[tuple[str, str, str]],
            hosts: tuple[str, ...]) -> Path:
    """pairs = [(marker, title, body), ...];域名轮转,保证独立来源数可控。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / f"{category}.db"
    store.migrate_articles_db(category, db_path=db_path)
    con = sqlite3.connect(db_path)
    try:
        for index, (marker, title, body) in enumerate(pairs):
            _insert(con, category, marker, hosts[index % len(hosts)], title, body,
                    author=f"作者{index}")
        con.commit()
    finally:
        con.close()
    rag.rebuild_index(category, db_path=db_path)
    return db_path


class BoomModel:
    """哨兵:离题选题被拦下后一次模型都不许调(被调用即红)。"""

    def __call__(self, *args, **kwargs):
        raise AssertionError("主题无相关素材时不得调用模型")


def _rank(db_path: Path, category: str, topic: str) -> tuple[list[dict], dict]:
    matches = rag.search(category, topic, limit=workflow.MATERIAL_POOL_LIMIT, db_path=db_path)
    return workflow._rank_materials(matches, db_path, topic=topic)


# ---------------------------------------------------------------- 尺子本身

def test_topic_relevance_uses_tokens_identical_to_fts():
    """取词口径必须与 ``rag.search`` 完全一致(同一分词器、同一 12 词上限)。"""
    assert rag.topic_tokens(MIX_TOPIC) == rag.tokenize(MIX_TOPIC)[: rag.TOPIC_TOKEN_LIMIT]
    assert rag.topic_tokens(MIX_TOPIC) == ["装修", "噪音"]


def test_topic_relevance_threshold_matrix():
    """四个象限逐个钉死:标题点题+密度够/只有标题/只有密度/都不够。"""
    dense_on = rag.topic_relevance(MIX_TOPIC, ON_TITLE, ON_BODY)
    assert dense_on["relevant"] is True and dense_on["title_hits"] >= 1

    # 标题点题但正文只在开头提了一次 ⇒ 不算相关(防标题党蹭题)
    title_only = rag.topic_relevance(MIX_TOPIC, ON_TITLE, "开头提一次装修噪音。" + "行业观察随笔。" * 160)
    assert title_only["title_hits"] >= 1 and title_only["per_1k"] < rag.PER_1K_WITH_TITLE, title_only
    assert title_only["relevant"] is False, title_only

    # 标题没点题但正文密集复述主题词 ⇒ 相关(生产语料里转述稿常见)
    dense_body = "装修噪音扰民的处理办法:噪音超标可投诉,装修时段要报备。" * 20
    dense_only = rag.topic_relevance(MIX_TOPIC, "本周社区记事", dense_body)
    assert dense_only["relevant"] is True and dense_only["title_hits"] == 0

    # 密度居中但标题没点题 ⇒ 不相关(探针实测健康主题都在 8/千字以上)
    middle = rag.topic_relevance(
        MIX_TOPIC, "社区记事", "小区装修有点噪音。" + "行业观察随笔。" * 120
    )
    assert middle["title_hits"] == 0 and middle["per_1k"] < rag.PER_1K_ONLY
    assert middle["relevant"] is False, middle


def test_unjudgeable_topic_skips_the_filter_instead_of_blocking_everything():
    """取词为空(如纯数字主题)⇒ 无尺子可用:如实放行并写明理由,不假装过滤。"""
    rel = rag.topic_relevance("3.5", "3.5 折的行业", "行业观察随笔。" * 60)
    assert rel["relevant"] is True and rel["tokens"] == 0
    assert "无可提取词" in rel["rule"]
    assert rag.topic_tokens("GPT") == ["gpt"], "拉丁词按小写整词取词,不算无词可用"


def test_hit_counting_is_non_overlapping_and_conservative():
    """命中计数用非重叠 findall ⇒ 数值偏保守,宁可少算不误留离题稿。"""
    # 「噪音声」取词 = 噪音/音声;正文「噪音声」只数到 1 次(音声被噪音占位后不再回看)
    rel = rag.topic_relevance("噪音声", "噪音声", "噪音声")
    assert rel["body_hits"] == 1 and rel["tokens"] == 2, rel
    assert rel["coverage"] == 0.5


def test_known_limit_title_borrowing_a_generic_token_still_counts():
    """已知边界(如实钉住,不藏):标题蹭到一个通用主题词 + 正文密度过线下限 ⇒ 判相关。

    生产探针里离题稿的 ``title_hits`` 实测都是 0,所以这条分支没在 10 题里造成误放行;
    但「高空抛物 赔偿 标准」这类含通用词(标准/赔偿)的主题理论上能被蹭题稿骗过。
    R64-2 的数据驱动选题会用「相关候选绝对数」再兜一层;真要收紧需先在快照上重新验证阈值。
    """
    borrowed = rag.topic_relevance(FALLBACK_TOPIC, FALLBACK_TITLE + "的标准", FALLBACK_BODY)
    assert borrowed["title_hits"] >= 1 and borrowed["relevant"] is True, borrowed


# ---------------------------------------------------------------- 离题候选不进池

def test_off_topic_candidates_never_occupy_material_slots(tmp_path):
    """混合池:6 相关 + 4 离题 ⇒ 离题稿不占槽、不计密度与独立来源,并如实记剔除数。"""
    pairs = [(f"on{i}", ON_TITLE, ON_BODY) for i in range(6)]
    pairs += [(f"off{i}", OFF_TITLE, OFF_BODY) for i in range(4)]
    db_path = _corpus(tmp_path, "mix", pairs, hosts=("a.test", "b.test", "c.test"))

    matches = rag.search("mix", MIX_TOPIC, limit=workflow.MATERIAL_POOL_LIMIT, db_path=db_path)
    assert len(matches) == 10, "两词都在正文里 ⇒ 十条都进候选池(FTS 判定不变)"

    materials, stats = workflow._rank_materials(matches, db_path, topic=MIX_TOPIC)
    rel = stats["relevance"]
    assert rel["searched"] == 10 and rel["kept"] == 6 and rel["dropped"] == 4, rel
    assert stats["searched_candidates"] == 10
    assert stats["candidates"] == 6 and stats["full_candidates"] == 6
    assert len(materials) == workflow.MATERIAL_TOP_K
    assert all(any(entry["id"] == m["id"] for entry in rel["off_topic_samples"]) is False
               for m in materials), "离题稿不得进选材"
    assert {m["title"] for m in materials} == {ON_TITLE}
    assert rel["off_topic_samples"], "离题稿要留证据(标题/命中/密度)"
    assert all(sample["per_1k"] < rag.PER_1K_ONLY for sample in rel["off_topic_samples"])
    # 两个既有门槛只数相关候选 ⇒ 阈值一字未动也能放行健康主题
    assert workflow._material_gate(stats, 6, 3) == []


def test_scores_carry_the_relevance_evidence(tmp_path):
    """每条选材分数都带 title_hits/per_1k ⇒ 回执里能一句话说清凭什么算相关。"""
    db_path = _corpus(tmp_path, "mix2", [(f"on{i}", ON_TITLE, ON_BODY) for i in range(6)],
                      hosts=("a.test", "b.test", "c.test"))
    _materials, stats = _rank(db_path, "mix2", MIX_TOPIC)
    for entry in stats["scores"]:
        assert entry["title_hits"] >= 1 and entry["per_1k"] >= rag.PER_1K_WITH_TITLE, entry


def test_or_fallback_offtopic_topic_is_blocked_with_zero_model_calls(tmp_path):
    """OR 回退现场(「高空抛物」的生产形状):6 篇全文、3 个独立来源,但全是离题稿。

    旧的取材门禁会放行(6 全文/3 来源都够)⇒ 每篇白烧约 4.5 分钟模型。
    现在离题稿不进池:density 与独立来源都是 0,直接拦下且零模型调用。
    """
    pairs = [(f"fb{i}", FALLBACK_TITLE, FALLBACK_BODY) for i in range(6)]
    db_path = _corpus(tmp_path, "fb", pairs, hosts=("a.test", "b.test", "c.test"))

    matches = rag.search("fb", FALLBACK_TOPIC, limit=workflow.MATERIAL_POOL_LIMIT, db_path=db_path)
    assert len(matches) == 6, "AND 空结果 + 取词>3 ⇒ search 回退 OR 召回(既有召回行为不变)"

    materials, stats = workflow._rank_materials(matches, db_path, topic=FALLBACK_TOPIC)
    assert stats["candidates"] == 0 and stats["full_candidates"] == 0
    assert stats["distinct_sources"] == 0
    assert stats["relevance"]["dropped"] == 6

    critical = workflow._material_gate(stats, 6, 3)
    assert any("取材密度" in item for item in critical), critical
    assert any("语料缺该主题素材" in item for item in critical), critical

    export_dir = tmp_path / "drafts"
    result = workflow.write_article(
        "fb", FALLBACK_TOPIC, model_call=BoomModel(), db_path=db_path,
        export_dir=export_dir, independent_review=False,
    )
    assert result["status"] == "insufficient_materials"
    assert result["model_calls"] == 0
    assert result["material_stats"]["relevance"]["dropped"] == 6
    assert not export_dir.exists(), "离题选题拦下时不得产出草稿"
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT COUNT(*) FROM writing_history").fetchone()[0]
    finally:
        con.close()
    assert rows == 0, "离题选题拦下时不得写版本历史"


def test_and_hit_topic_keeps_offtopic_docs_out_of_the_pool(tmp_path):
    """AND 命中真相关稿时**不回退 OR** ⇒ 蹭词离题稿根本不进候选(相关度尺子不越权)。

    这一条钉住"改动没动召回":离题稿被剔只发生在它们真进了候选池的场合(见上一条)。
    """
    pairs = [(f"fb_on{i}", FALLBACK_ON_TITLE, FALLBACK_ON_BODY) for i in range(6)]
    pairs += [(f"fb_off{i}", FALLBACK_TITLE, FALLBACK_BODY) for i in range(3)]
    db_path = _corpus(tmp_path, "fb2", pairs, hosts=("a.test", "b.test", "c.test"))
    _materials, stats = _rank(db_path, "fb2", FALLBACK_TOPIC)
    assert stats["relevance"]["searched"] == 6
    assert stats["relevance"]["kept"] == 6 and stats["relevance"]["dropped"] == 0
    assert stats["full_candidates"] == 6
    assert workflow._material_gate(stats, 6, 3) == []


def test_precheck_shares_the_same_relevance_filter(tmp_path):
    """预筛与写作同一条取材链:离题材料里的锚点不再被算作"可绑定一手锚点"。"""
    pairs = [(f"fb{i}", FALLBACK_TITLE, FALLBACK_BODY) for i in range(6)]
    db_path = _corpus(tmp_path / "pc", "fb3", pairs, hosts=("a.test", "b.test", "c.test"))
    pre = precheck.precheck_topic("fb3", FALLBACK_TOPIC, db_path=db_path)
    assert pre["material_stats"]["relevance"]["dropped"] == 6
    assert pre["full_bodies_selected"] == 0
    assert pre["cross_verified_total"] == 0, "离题稿的锚点不得算印证"
    assert pre["first_hand_total"] == 0
