# -*- coding: utf-8 -*-
"""R29:建模模型自报的「逐字引句」,修创作链的「原文引句未绑定」根因。

2026-11-18 生产拒稿实测(social 养老稿 3 版全 rejected):
- claim 把两篇材料的事实并成一句 → 自动绑定只能绑到重叠最高的那句,
  数字落在绑定引句 ±1 句之外 → `numbers_outside_quote` → unverified → critical;
- 另一条 claim 与来源任何一句的重叠都不到 0.34 → 引句绑不上。

修法不是放宽门禁,而是让模型指明「依据的是来源哪一句」,由 verifier **逐字**校验:
引句必须在来源里连续存在,且与 claim 的重叠仍须 ≥ QUOTE_MIN_OVERLAP;
不满足则退回自动绑定。下面每组断言都钉住「放宽就红」。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ttxt.writing import prompts, verifier, workflow  # noqa: E402

S1 = "记者走访多地发现,养老金待遇差距与参保策略密切相关,不同群体的缴费年限和缴费基数不同,退休后待遇自然不同。"
S2 = "公开数据显示,部分地区的基础养老金水平仍低于全国平均。"
S3 = "一位网友测算工龄36年退休金约4268元。"
TEXT = S1 + S2 + S3
CLAIM = "记者走访多地发现,养老金待遇差距与参保策略密切相关,一位网友测算工龄36年退休金约4268元。"
ARTICLE = {"id": 1, "title": "养老金差距", "url": "https://example.com/a", "text": TEXT}


def test_auto_binding_misses_numbers_when_claim_merges_sources():
    got = verifier.verify_claim(CLAIM, ARTICLE)
    assert got["credibility"] == "unverified"
    assert got["quote"] == S1, "自动绑定取重叠最高的句子,数字不在这一段"
    assert set(got["checks"]["numbers"]["missing"]) == {"36", "4268元"}
    assert got["checks"]["quote_source"] == "auto_bound"


def test_verbatim_quote_from_model_binds_the_sentence_holding_the_numbers():
    got = verifier.verify_claim(CLAIM, ARTICLE, quote=S3)
    assert got["credibility"] == "verified"
    assert got["quote"] == S3
    assert got["checks"]["quote_source"] == "model_supplied"
    assert got["checks"]["numbers"]["missing"] == []


def test_non_verbatim_quote_is_not_trusted():
    tampered = S3.replace("4268", "4269")
    got = verifier.verify_claim(CLAIM, ARTICLE, quote=tampered)
    assert got["checks"]["quote_source"] == "auto_bound", "改一个字就不是逐字引句,必须退回自动绑定"
    assert got["credibility"] == "unverified"


def test_verbatim_quote_with_low_overlap_is_not_trusted():
    # S2 是原文,但与 claim 词面重叠不足 0.34 ⇒ 不能拿它当绑定依据
    got = verifier.verify_claim(CLAIM, ARTICLE, quote=S2)
    assert got["checks"]["quote_source"] == "auto_bound"
    assert got["quote"] == S1
    assert got["credibility"] == "unverified"


def test_quote_cannot_smuggle_anchors_that_are_not_in_it():
    # 引句逐字、重叠够,但 claim 里的数字不在引句窗口内 ⇒ 仍然不通过
    claim = "一位网友测算工龄36年退休金约4268元,另有9999人受影响。"
    got = verifier.verify_claim(claim, ARTICLE, quote=S3)
    assert got["checks"]["numbers"]["missing"] == ["9999人"]
    assert got["credibility"] == "unverified"


def test_parse_claims_keeps_quote_only_when_present():
    raw = '[{"claim":"A","article_id":1,"quote":"原文句"},{"claim":"B","article_id":2}]'
    assert workflow.parse_claims(raw) == [
        {"claim": "A", "article_id": 1, "quote": "原文句"},
        {"claim": "B", "article_id": 2},
    ]


def test_prompts_demand_verbatim_single_source_evidence():
    claims = prompts.claims_prompt("草稿正文", [1, 2])
    assert "逐字" in claims and "quote" in claims
    draft = prompts.draft_prompt("题目", "大纲", "[1] 材料正文")
    assert "一篇" in draft, "写作时就要禁止把多篇材料的数字拼进同一句"
    assert "预测" in draft, "「预测/模拟」不能成为编造统计数字的许可证"


def test_citation_markers_are_not_fact_numbers():
    """5 条事实全因句末 [n] 被判数字缺失 —— 引用编号是元数据,不是事实。"""
    article = {"id": 1, "title": "t", "url": "u", "text": "该模型已完成生成式人工智能服务备案。"}
    claim = "杭州云知声自研的大模型已完成生成式人工智能服务备案 [4]。"
    got = verifier.verify_claim(claim, article)
    assert got["checks"]["numbers"]["missing"] == []
    assert "4" not in got["anchors"]["numbers"]
    assert verifier.extract_anchors("完成备案【12】")["numbers"] == []


def test_person_anchor_skips_adverb_between_name_and_verb():
    anchors = verifier.extract_anchors("随着华盛顿不断强调芯片供应必须服从其规则")
    assert anchors["persons"] == ["华盛顿"], "不能截成「盛顿不断」这种假人名"
    assert verifier.extract_anchors("专家表示支持")["persons"] == ["专家"]


def test_date_components_match_within_the_same_sentence():
    text = "11月7日至8日，2026首届文化智能国际会议暨第二届文化智能大会在北京召开。"
    claim = "2026年11月7日至8日，首届文化智能国际会议在北京召开 [2]。"
    got = verifier.verify_claim(claim, {"id": 1, "title": "t", "url": "u", "text": text})
    assert got["checks"]["dates"]["missing"] == []
    # 伪造日期仍必须被拦住
    bad = verifier.verify_claim(
        "2026年11月15日，首届文化智能国际会议在北京召开 [2]。",
        {"id": 1, "title": "t", "url": "u", "text": text},
    )
    assert bad["checks"]["dates"]["missing"] == ["2026年11月15日"]
    assert bad["credibility"] == "unverified"


def test_empty_evidence_chain_is_blocked_not_silent():
    """生产实测(2026-11-18 technology 稿):claims 提取返回空数组,3 版全走了「只剩模型评审」的门禁."""
    body = "SK海力士龙仁工厂投资额达数十万亿韩元,首座厂房规划高度约150米,2027年投产。"
    issues, critical = verifier.deterministic_findings([], "算力自主", body)
    assert any("evidence_empty" in item for item in critical)
    # 纯观点/无锚点正文不触发这条(不把无事实可核的稿子一律钉死)
    _, critical2 = verifier.deterministic_findings([], "一个看法", "我认为这件事值得讨论。")
    assert critical2 == []


def test_claims_extraction_gets_a_realistic_token_budget():
    """逐字引句版 claims(最多 12 条)在旧 400 token 下必然截断→JSON 解析失败→空证据链。"""
    assert workflow.CLAIMS_MAX_TOKENS >= 1200


def test_draft_prompt_forbids_tense_laundering_of_future_events():
    text = prompts.draft_prompt("t", "o", "m")
    assert "将举办" in text and "不得写成已发生" in text
    assert "当前现实时间" in text
