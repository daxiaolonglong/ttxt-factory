# -*- coding: utf-8 -*-
"""P13 批次4(账本 T-15):归属必须真实存在于来源,否则不得走「转述」通道。

修复前的漏洞(两位 AI 独立确认、第一轮 10/10 判「不贴切」):
  `verifier.is_opinion()` 只看**句子自身**有没有「业内人士预计/或将/显然」这类归属词,
  从不检查**来源原句里是否真的有这个归属** ⇒ 10 张伪造归属卡被贴「转述」、
  `missing_dims` 全空、`deterministic_findings` 一条 critical 都不产生 ⇒ 根本不进事实门禁。

本文件钉死的新行为(只收紧,不放宽):
1. 路由前先做**归属存在性核验**:第三/四方归属(据 X 报道/据悉/业内人士预计/外媒报道…)
   必须在**绑定引句 ±1 句**的连续窗口内**逐字**找到;
2. 找不到 ⇒ 不得路由 opinion,改走事实核验路径,并产生一条带机器可读标识
   `attribution_not_in_source` 的 critical,`missing_dims` 至少含 `attribution`;
3. 归属真实存在于窗口内 ⇒ 仍按 opinion 路由(不得误杀真实转述);
4. 第一人称自述(我认为/笔者…)的出处就是作者本人,来源里本来就不该有,不做存在性检查;
5. 归属存在只是**路由条件**,不是通过条件:找到归属不会让任何句子「算通过」核验;
6. 答案钥匙(golden_set_v2.jsonl 的 expected_by_construction)跟着现实改,但只改这 10 行,
   其余字段、其余 51 行、AI sidecar、纸面标签证据一律零差异 —— 用 SHA-256 钉死。

不用模型判官,全部确定性断言。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from ttxt.writing import verifier

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "10_tests" / "golden"
CARDS_PATH = GOLDEN / "golden_set_v2.jsonl"
SIDECAR_PATH = GOLDEN / "golden_set_v2_ai_adjudication.jsonl"
PRINTED_TOKENS_PATH = GOLDEN / "adjudications" / "printed_sheet_tokens_20261116.jsonl"

_spec = importlib.util.spec_from_file_location(
    "golden_v2_harness_b4", ROOT / "scripts" / "golden_v2_harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

# 10 张伪造归属卡(账本 T-15),修复前机器贴「转述」,修复后必须被事实门禁拦下
T15_CARDS = ["v2f06", "v2f12", "v2f17", "v2h04", "v2h09", "v2h15",
             "v2t05", "v2t11", "v2t17", "v2t23"]

# 修复前(2026-11-17 T-15 取证)这 10 张的答案钥匙,仅作对照与迁移脚本的旧值凭证
OLD_KEY_FOR_THE_TEN = {cid: "opinion" for cid in T15_CARDS}

# 答案钥匙(T-15 之后):10 张 opinion → unverified,其余 51 张一字未动
ANSWER_KEY = {
    "v2t01": "verified", "v2t02": "unverified", "v2t03": "unverified",
    "v2t04": "unverified", "v2t05": "unverified", "v2t06": "verified",
    "v2t07": "unverified", "v2t08": "unverified", "v2t09": "unverified",
    "v2t10": "unverified", "v2t11": "unverified", "v2t12": "verified",
    "v2t13": "unverified", "v2t14": "unverified", "v2t15": "unverified",
    "v2t16": "unverified", "v2t17": "unverified", "v2t18": "verified",
    "v2t19": "unverified", "v2t20": "unverified", "v2t21": "unverified",
    "v2t22": "unverified", "v2t23": "unverified", "v2t24": "unverified",
    "v2t25": "unverified", "v2f01": "verified", "v2f02": "unverified",
    "v2f03": "unverified", "v2f04": "unverified", "v2f05": "unverified",
    "v2f06": "unverified", "v2f07": "verified", "v2f08": "unverified",
    "v2f09": "unverified", "v2f10": "unverified", "v2f11": "unverified",
    "v2f12": "unverified", "v2f13": "verified", "v2f14": "unverified",
    "v2f15": "unverified", "v2f16": "unverified", "v2f17": "unverified",
    "v2f18": "unverified", "v2f19": "unverified", "v2h01": "verified",
    "v2h02": "unverified", "v2h03": "unverified", "v2h04": "unverified",
    "v2h05": "verified", "v2h06": "unverified", "v2h07": "unverified",
    "v2h08": "unverified", "v2h09": "unverified", "v2h10": "verified",
    "v2h11": "unverified", "v2h12": "unverified", "v2h13": "unverified",
    "v2h14": "unverified", "v2h15": "unverified", "v2h16": "unverified",
    "v2h17": "unverified",
}

# 2026-11-17 T-15 修复**之前**从 git HEAD 取的完整性指纹。
# 卡片:61 行逐行 json.loads 后去掉 expected_by_construction 再 json.dumps(ensure_ascii=False) 拼接;
# 这两个文件必须一个字节都不许变。
# SIDECAR_SHA256 于 R61(2026-11-21)重新锚定:12 张分歧卡第二轮重裁只改 ai_recheck_*/machine_now
# 等裁定字段(证据 10_tests/golden/adjudications/r61_recheck_local_round2.json),
# 卡片文件与纸面标签证据的哈希钉未动;sidecar 的 hash 重新锚定后继续执行「不许悄悄改」职责。
CARDS_FIELDS_EXCEPT_ANSWER_KEY_SHA256 = (
    "8dceea47f847a5e3a4c0f3989c187b6140e61136679f91414e45a9f6e29b9d29")
SIDECAR_SHA256 = "dedb6bad2dde7d14b896e9591a66b2b0418137d465ffc4d37b31f6b34f1c7682"
PRINTED_TOKENS_SHA256 = "2574ce220ae0db350519a578857b84e57bea4f3b1c075e34df5ab9594ca9d531"

HUMAN_FIELDS = ("human_adjudicated", "human_verdict", "human_fact_support", "human_confirms_tier")


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#"):
            rows.append(json.loads(raw))
    return rows


@pytest.fixture(scope="module")
def cards() -> list[dict]:
    loaded, errors = harness.load_cards(CARDS_PATH)
    assert not errors, errors
    return loaded


def _article(card: dict) -> dict:
    return {"id": card.get("source_article_id"), "title": card.get("source_title") or "",
            "url": card.get("source_url") or "", "text": card.get("source_text") or ""}


def _evidence(card: dict) -> dict:
    return verifier.verify_claim(card["claim"], _article(card))


def _critical(card: dict) -> list[str]:
    _issues, critical = verifier.deterministic_findings([_evidence(card)], card["claim"], card["claim"])
    return critical


# --------------------------------------------------------------- 1. 十张伪造归属卡:必须进门禁

@pytest.mark.parametrize("card_id", T15_CARDS)
def test_fabricated_attribution_card_is_no_longer_routed_as_opinion(cards, card_id):
    card = next(c for c in cards if c["card_id"] == card_id)
    markers = verifier.is_opinion(card["claim"])
    assert markers, f"{card_id}:句子里本来就有归属词,否则这条回归没意义"
    result = harness.machine_result(card)
    evidence = _evidence(card)
    assert evidence["kind"] == "fact", f"{card_id}:{markers} 仍被路由成转述"
    assert result["credibility"] == "unverified", result
    attribution = evidence["checks"].get("attribution") or {}
    assert attribution.get("missing"), f"{card_id}:必须记录来源里找不到的归属"
    assert set(attribution.get("found") or []) == set(), f"{card_id}:来源里有归属却报缺失"
    assert any(m in markers for m in attribution["missing"]), attribution
    assert any(dim.startswith("attribution:") for dim in result["missing_dims"]), result["missing_dims"]


@pytest.mark.parametrize("card_id", T15_CARDS)
def test_fabricated_attribution_card_produces_a_critical_with_the_machine_id(cards, card_id):
    card = next(c for c in cards if c["card_id"] == card_id)
    critical = _critical(card)
    hits = [line for line in critical if "attribution_not_in_source" in line]
    assert hits, f"{card_id}:没有带 attribution_not_in_source 标识的 critical:{critical}"
    assert any(line.startswith("事实未通过来源核验") for line in critical), critical


def test_the_ten_cards_answer_key_moved_to_unverified(cards):
    by_id = {c["card_id"]: c for c in cards}
    for card_id in T15_CARDS:
        assert by_id[card_id]["expected_by_construction"] == "unverified", card_id
        assert by_id[card_id]["kind"] == "opinion_attributed", card_id
        assert by_id[card_id]["expected_by_construction"] != OLD_KEY_FOR_THE_TEN[card_id]


# --------------------------------------------------------- 2. 真实转述不得误杀(归属真在来源里)

GENUINE_SOURCE_CARD = "v2t01"          # 真实来源:来源里本来就写着「据数码闲聊站爆料」
GENUINE_CLAIM = ("据数码闲聊站爆料,国产TOP5厂商(华为、OPPO、vivo、小米、荣耀)已经"
                 "全部完成阔直板立项,2027年会集中交卷")


def test_genuine_paraphrase_still_routes_to_opinion(cards):
    card = next(c for c in cards if c["card_id"] == GENUINE_SOURCE_CARD)
    text = card["source_text"]
    assert "据数码闲聊站爆料" in text, "取材自真实来源,短语必须逐字在来源里"
    evidence = verifier.verify_claim(GENUINE_CLAIM, _article(card))
    assert evidence["kind"] == "opinion", evidence["checks"]
    assert evidence["credibility"] == "opinion"
    assert "据数码闲聊站爆料" in evidence["checks"]["opinion_markers"]
    assert evidence["checks"]["attribution"]["missing"] == []
    assert evidence["checks"]["attribution"]["found"] == ["据数码闲聊站爆料"]
    _issues, critical = verifier.deterministic_findings([evidence], GENUINE_CLAIM, GENUINE_CLAIM)
    assert not any("attribution_not_in_source" in line for line in critical), critical
    # 归属存在只是路由条件,不是通过条件:它不会给任何句子盖「已核实」的章
    assert evidence["credibility"] != "verified"


def test_no_golden_card_escapes_the_gate_through_a_fabricated_attribution(cards):
    """61 张里不再有任何一张靠「来源里根本没有的归属」逃开门禁;真实归属卡 0 张(如实记录)。"""
    escaped = []
    genuine = []
    for card in cards:
        evidence = _evidence(card)
        markers = verifier.is_opinion(card["claim"])
        if evidence["kind"] != "opinion":
            continue
        attribution = evidence["checks"].get("attribution") or {}
        if markers and attribution.get("found"):
            genuine.append(card["card_id"])
        else:
            escaped.append(card["card_id"])
    assert escaped == [], f"仍在靠伪造归属放行:{escaped}"
    assert genuine == [], "黄金集 61 张里没有「归属真实存在于来源」的转述卡(真实转述反例见上一条测试)"


# --------------------------------------------------- 3. 存在性核验的边界:窗口、逐字、第一人称

def test_attribution_must_be_inside_the_quote_window_not_just_anywhere_in_the_source(cards):
    """归属在来源里、但落在绑定引句 ±1 句之外 ⇒ 同样不得走「转述」(与数字/日期锚定同一把尺子)。"""
    card = next(c for c in cards if c["card_id"] == "v2t25")   # 同一篇真实来源,引句在别处
    text = card["source_text"]
    quote = "一个被华为单点验证的新形态，正在变成全行业的“同题作文”。"
    window, _offset = verifier.anchor_window(text, quote, text.index(quote))
    assert "据数码闲聊站爆料" in text and "据数码闲聊站爆料" not in window
    claim = "据数码闲聊站爆料,一个被华为单点验证的新形态,正在变成全行业的同题作文"
    evidence = verifier.verify_claim(claim, {"id": 1, "title": "", "url": "https://x/1", "text": text})
    assert evidence["kind"] == "fact", "窗口外的归属不得放行"
    assert evidence["checks"]["attribution"]["missing"] == ["据数码闲聊站爆料"]
    assert evidence["credibility"] != "opinion"


def test_first_person_opinion_is_not_required_to_appear_in_the_source():
    """「我认为/笔者」的出处就是作者自己,来源里没有是正常的,照旧走观点通道。"""
    text = "星海科技公司发布新一代开源模型,首批装机量达到 88 万台。"
    for claim in ("我认为,该模型将成为行业标杆", "笔者以为,这批产能被严重高估了"):
        evidence = verifier.verify_claim(claim, {"id": 1, "title": "", "url": "https://x/1", "text": text})
        assert evidence["kind"] == "opinion", claim
        assert evidence["credibility"] == "opinion"
        assert not (evidence["checks"].get("attribution") or {}).get("missing"), claim


def test_attribution_check_is_verbatim_and_never_fuzzy():
    """逐字匹配:换一种写法(近义/少字)不算找到,一律走门禁。"""
    text = "业内人士预计,这套工艺明年才能量产。"
    for claim in ("业内人士或将,这套工艺明年才能量产",   # 来源写「预计」,不是「或将」
                  "分析人士预计,这套工艺明年才能量产"):   # 来源写「业内人士」,不是「分析人士」
        evidence = verifier.verify_claim(claim, {"id": 1, "title": "", "url": "https://x/1", "text": text})
        assert evidence["kind"] == "fact", claim
        assert evidence["checks"]["attribution"]["missing"], claim
    found = verifier.verify_claim("业内人士预计,这套工艺明年才能量产",
                                  {"id": 1, "title": "", "url": "https://x/1", "text": text})
    assert found["kind"] == "opinion"
    assert found["checks"]["attribution"]["found"] == ["业内人士预计"]


def test_attribution_without_any_source_is_never_routed_as_opinion():
    """没有来源卡片时,第三/四方归属也不许靠「转述」标签蒙混过关(仍记 no_source + 归属缺失)。"""
    evidence = verifier.verify_claim("据业内人士透露,该型号将于下月量产", None)
    assert evidence["credibility"] == "no_source"
    assert evidence["kind"] == "fact"
    assert evidence["checks"]["attribution"]["missing"]
    _issues, critical = verifier.deterministic_findings([evidence], "标题", "正文正文正文")
    assert any("attribution_not_in_source" in line for line in critical), critical
    assert any(line.startswith("无来源断言") for line in critical), critical


# --------------------------------------------------- 3b. 拦下了还得让人看见:导出证据链表

def test_missing_attribution_shows_up_in_the_exported_evidence_table(cards):
    """机器拦下只是第一步:编辑看的草稿证据链表里也必须写明 `attribution:<归属>` 缺了。

    少了这一列,导出稿只写「核验=unverified」,人工复核看不出到底是数字还是归属没过,
    等于把 T-15 的证据藏进人看不见的地方。
    """
    from ttxt.writing import export

    card = next(c for c in cards if c["card_id"] == "v2f06")
    evidence = _evidence(card)
    markdown = export.render_markdown(
        "technology", "t15-export", "标题", "主题", card["claim"], [evidence],
        {"score": 0, "issues": [], "critical": []}, version=1)
    assert "attribution:业内人士预计" in markdown, markdown
    assert "unverified" in markdown


# ----------------------------------------------------------- 4. T-03 锚定回归(不造新轮子)

def test_v2t13_still_missing_the_number_ten(cards):
    card = next(c for c in cards if c["card_id"] == "v2t13")
    result = harness.machine_result(card)
    assert result["credibility"] == "unverified"
    assert "numbers:10" in result["missing_dims"], result["missing_dims"]


def test_v2h01_is_still_verified_not_routed_as_opinion(cards):
    card = next(c for c in cards if c["card_id"] == "v2h01")
    result = harness.machine_result(card)
    assert result["credibility"] == "verified", result
    assert _evidence(card)["kind"] == "fact"
    assert result["missing_dims"] == []


# --------------------------------- 5. 答案钥匙完整性:除这 10 行的 expected_by_construction 外零差异

def test_answer_key_is_pinned_for_all_61_cards(cards):
    got = {c["card_id"]: c["expected_by_construction"] for c in cards}
    assert got == ANSWER_KEY
    changed = {cid for cid, value in got.items() if OLD_KEY_FOR_THE_TEN.get(cid) and value != OLD_KEY_FOR_THE_TEN[cid]}
    assert changed == set(T15_CARDS), sorted(changed)
    assert all(c["expected_by_construction"] == harness.machine_result(c)["credibility"] for c in cards)


def test_every_other_card_field_is_byte_identical_to_the_pre_fix_key(cards):
    """除 expected_by_construction 外,61 张卡的每个字段(claim/source_excerpt/source_url/quote*/kind/
    source_tier/human_*/note…)必须与修复前逐字一致 —— 用 HEAD 上取的 SHA-256 钉死。"""
    dumps = []
    for raw in CARDS_PATH.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        card = json.loads(raw)
        body = dict(card)
        body.pop("expected_by_construction")
        dumps.append(json.dumps(body, ensure_ascii=False))
    assert len(dumps) == 61
    digest = hashlib.sha256("\n".join(dumps).encode("utf-8")).hexdigest()
    assert digest == CARDS_FIELDS_EXCEPT_ANSWER_KEY_SHA256, "答案钥匙之外还改了别的字段/行序"


def test_human_adjudication_is_still_empty_and_ai_sidecar_is_untouched(cards):
    for card in cards:
        assert card["human_adjudicated"] is False, card["card_id"]
        for field in ("human_verdict", "human_fact_support", "human_confirms_tier"):
            assert card[field] is None, (card["card_id"], field)
    sidecar_text = SIDECAR_PATH.read_text(encoding="utf-8")
    assert hashlib.sha256(sidecar_text.encode("utf-8")).hexdigest() == SIDECAR_SHA256
    rows = _read_jsonl(SIDECAR_PATH)
    assert len(rows) == 61
    assert all(not [k for k in row if k.startswith("human_")] for row in rows)
    assert hashlib.sha256(PRINTED_TOKENS_PATH.read_text(encoding="utf-8").encode("utf-8")).hexdigest(
    ) == PRINTED_TOKENS_SHA256
