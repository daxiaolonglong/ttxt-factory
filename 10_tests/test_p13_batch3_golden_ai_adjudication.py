"""P13 批次3:AI 辅助初审 sidecar 的护栏 + T-03 错标签回归测试(2026-11-17)。

三件事,各管各的,不许互相顶替:

1. **AI 口径的护栏**:61 张 AI 裁定只许待在
   `10_tests/golden/golden_set_v2_ai_adjudication.jsonl` 的 `ai_*` 字段里。
   卡片文件的 `human_*` 必须仍然全是「未裁定」;sidecar 里出现 `human_*` = 冒充人工 = 模式错误。
   质量门禁(≥50 人工裁定)照旧不过,退出码照旧 2 —— AI 初审不得让 CI 变绿。
2. **T-03 的回归**:2026-11-17 修掉了 6 张「造假句被放行」的卡。这里把
   其中 5 张(第 6 张 v2h01 是正样本,走另一条断言)钉成回归样本,
   验证系统现在**拦得住**这些错误标签,以后改回去就红。
3. **T-15 的登记**:剩下 10 张 `opinion_attributed` 卡走的是「转述」通道 ——
   伪造的「业内人士预计」把句子送进 opinion,事实门禁根本不跑。
   AI 初审 10/10 判机器贴得不贴切。这是**已知未修的缺陷**,这里只登记现状,
   不在本测试里偷偷改路由器,也不改答案钥匙(改了 A/B 口径就失真)。
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "10_tests" / "golden"
CARDS_PATH = GOLDEN / "golden_set_v2.jsonl"
SIDECAR_PATH = GOLDEN / "golden_set_v2_ai_adjudication.jsonl"
SHEET_TOKENS_PATH = GOLDEN / "adjudications" / "printed_sheet_tokens_20261116.jsonl"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load("golden_v2_harness_b3", ROOT / "scripts" / "golden_v2_harness.py")
generator = _load("golden_v2_ai_adjudicate_b3", ROOT / "scripts" / "golden_v2_ai_adjudicate.py")


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


@pytest.fixture(scope="module")
def sidecar() -> list[dict]:
    return _read_jsonl(SIDECAR_PATH)


@pytest.fixture(scope="module")
def machine_now(cards) -> dict[str, str]:
    return {c["card_id"]: harness.machine_result(c)["credibility"] for c in cards}


# ---------------------------------------------------------------- 1. AI 口径护栏

@pytest.fixture(scope="module")
def ai(sidecar):
    """别名:重判与口径测试里用 `ai` 指代按 card_id 索引的 sidecar 字典。"""
    return {r["card_id"]: r for r in sidecar}


def test_sidecar_covers_every_card_exactly_once(cards, sidecar):
    ids = [r["card_id"] for r in sidecar]
    assert len(ids) == len(set(ids)) == len(cards) == 61
    assert set(ids) == {c["card_id"] for c in cards}
    assert all(r["ai_adjudicated"] is True for r in sidecar)


def test_sidecar_must_not_carry_human_fields(cards, sidecar):
    """AI 裁定冒充人工裁定 = 红线。sidecar 干净,卡片文件也照旧全空。"""
    for row in sidecar:
        leaked = [k for k in row if k.startswith("human_")]
        assert not leaked, f"{row['card_id']} 的 sidecar 混进了 human_* 字段 {leaked}"
    for card in cards:
        assert card["human_adjudicated"] is False, card["card_id"]
        for field in ("human_verdict", "human_fact_support", "human_confirms_tier"):
            assert card[field] is None, f"{card['card_id']}.{field} 被写了值"


def test_ai_adjudicator_is_marked_as_ai(cards, sidecar):
    assert {r["ai_adjudicator"] for r in sidecar} == {"chatgpt-assist"}
    for row in sidecar:
        assert "不是人工裁定" in row["ai_adjudicator_note"]
        # 原始时间戳没回传就写「录入时间 + 依据」,不许编时间
        assert "不伪造" in row["ai_at_basis"]
        assert row["ai_at"]


def test_sidecar_records_current_machine_labels_or_is_flagged_drift(cards, sidecar, machine_now):
    """sidecar 抄的机器标签必须跟今天的机器结论一致;不一致就是判据过期,得重裁。"""
    drift = [r["card_id"] for r in sidecar if r["machine_now"] != machine_now[r["card_id"]]]
    assert drift == [], f"机器结论已变,以下卡片的 AI 判据过期需重裁:{drift}"


def test_basis_label_equals_what_was_printed_on_the_sheet(sidecar):
    """① 的判据 = 打勾单纸上印的短词,不是今天的机器结论(否则就是伪造判据)。"""
    printed = {r["card_id"]: r for r in _read_jsonl(SHEET_TOKENS_PATH)}
    assert len(printed) == 61
    for row in sidecar:
        sheet = printed[row["card_id"]]
        assert row["sheet_printed_machine_token"] == sheet["printed_machine_token"]
        assert row["machine_at_adjudication"] == generator.TOKEN_TO_LABEL[sheet["printed_machine_token"]]
        assert row["ai_row_no"] == sheet["printed_row"]
        stale = row["machine_at_adjudication"] != row["machine_now"]
        assert row["ai_basis_stale"] is stale
        if stale:
            assert row["ai_verdict_current_reason"], f"{row['card_id']}:判据过期却没写重述理由"
            assert row["needs_human_review"] is True


def test_stale_basis_set_is_exactly_the_six_t03_cards(sidecar):
    """纸面标签与今日标签不一致的,必须正好是 T-03 改过结论的那 6 张。"""
    assert sorted(r["card_id"] for r in sidecar if r["ai_basis_stale"]) == [
        "v2h01", "v2h02", "v2h03", "v2h16", "v2t13", "v2t14"]
    for row in sidecar:
        if not row["ai_basis_stale"]:
            assert row["ai_verdict_current"] == row["ai_verdict"]


def test_reasons_exist_for_every_rejected_verdict(sidecar):
    """AI 判「机器贴得不贴切」的 15 张必须带理由;没理由的 46 张理由留空,不编。"""
    rejected = [r for r in sidecar if r["ai_verdict"] == "驳回"]
    assert len(rejected) == 15
    assert all(r["ai_reason"].startswith("AI 理由:") for r in rejected)
    assert all(r["ai_reason"] == "" for r in sidecar if r["ai_verdict"] == "通过")


def test_generator_reproduces_the_committed_sidecar(tmp_path, cards):
    """重跑生成器必须逐字得到同一份裁定(只允许录入时间不同)—— 转录不可漂。"""
    out = tmp_path / "again.jsonl"
    assert generator.main(["--out", str(out)]) == 0
    fresh = _read_jsonl(out)
    assert len(fresh) == 61
    keys = [k for k in fresh[0] if k not in ("ai_at", "ai_recheck_at")]
    assert [{k: r[k] for k in keys} for r in fresh] == [{k: r[k] for k in keys}
                                                        for r in _read_jsonl(SIDECAR_PATH)]


def test_recheck_covers_exactly_the_12_disputed_cards(ai):
    """两版打架的 12 张必须都带回重判,且**判据是今日标签**,不是 2026-11-16 纸面旧标签。"""
    disputed = {"v2f06", "v2f12", "v2f17", "v2h01", "v2h03", "v2h04", "v2h09", "v2h15",
                "v2t05", "v2t11", "v2t17", "v2t23"}
    rechecked = {cid for cid, r in ai.items() if r["ai_recheck_verdict"] is not None}
    assert rechecked == disputed, rechecked ^ disputed
    zh = {"verified": "能核实", "unverified": "对不上", "no_source": "没出处", "opinion": "转述"}
    for cid in rechecked:
        row = ai[cid]
        assert row["ai_recheck_basis_label"] == zh[row["machine_now"]], cid
        assert row["ai_recheck_rubric"] and "①b" in row["ai_recheck_rubric"], cid
        assert row["ai_recheck_flag"] is None, (cid, row["ai_recheck_flag"])
        assert row["ai_recheck_reason"].startswith("AI 重判理由"), cid
        assert "human_" not in "".join(k for k in row if k.startswith("ai_recheck")), cid


def test_recheck_keeps_ten_fabricated_attribution_cards_rejected(ai):
    """钉死口径 ①b 下:来源里没有那个归属 ⇒ 10 张伪造归属卡仍判「不贴切」,机器贴「转述」= T-15 漏洞。"""
    ten = ["v2f06", "v2f12", "v2f17", "v2h04", "v2h09", "v2h15", "v2t05", "v2t11", "v2t17", "v2t23"]
    for cid in ten:
        assert ai[cid]["ai_recheck_verdict"] == "驳回", cid
        assert ai[cid]["ai_recheck_basis_label"] == "转述", cid
        assert ai[cid]["machine_now"] == "opinion", cid
    assert ai["v2h01"]["ai_recheck_verdict"] == "通过"
    assert ai["v2h03"]["ai_recheck_verdict"] == "通过"
    # 重判不改历史 ai_verdict,也不改人工字段
    assert ai["v2h03"]["ai_verdict"] == "驳回" and ai["v2h01"]["ai_verdict"] == "通过"


def test_recheck_does_not_move_quality_conclusion(cards, ai):
    """重判后人工口径仍是 0、质量结论仍 NOT_AVAILABLE、退出码仍 2;AI 口径仍卡在 A/B <50%。"""
    metrics = harness.evaluate(cards, ai)
    assert metrics["human_adjudicated_count"] == 0
    assert metrics["quality_claim"] == "NOT_AVAILABLE"
    assert metrics["quality_claim_ai"] == "NOT_AVAILABLE"
    assert metrics["ai_gate_failures"] == ["AI 确认的 A/B 级独立证据占比 0.279 < 0.5"]


def test_generator_refuses_stale_verdict_without_restated_reason(cards, monkeypatch):
    """把某张「判据已过期」的重述理由删掉,生成器必须报错而不是沿用旧判定。"""
    monkeypatch.delitem(generator.CURRENT_BASIS_REREAD, "v2t13")
    sheet = generator.load_sheet_tokens(SHEET_TOKENS_PATH)
    machine = {c["card_id"]: harness.machine_result(c)["credibility"] for c in cards}
    _, errors = generator.build_rows(cards, machine, sheet)
    assert any("v2t13" in e and "重述" in e for e in errors), errors


# ---------------------------------------------------------------- 2. 双口径门禁

def test_dual_caliber_metrics_and_gate_stays_red(cards, sidecar):
    ai = {r["card_id"]: r for r in sidecar}
    metrics = harness.evaluate(cards, ai)
    assert metrics["human_adjudicated_count"] == 0
    assert metrics["ai_adjudicated_count"] == 61
    # 人工口径:门禁照旧三条不过,质量结论不可用
    assert metrics["quality_claim"] == "NOT_AVAILABLE"
    assert len(metrics["gate_failures"]) == 3
    assert metrics["machine_vs_human"]["accuracy"] is None
    # AI 口径:数量够了,但 A/B 独立证据占比仍卡在 17/61 —— T-02 未解
    assert metrics["ai_gate_failures"] == [
        f"AI 确认的 A/B 级独立证据占比 0.279 < {harness.MIN_INDEPENDENT_SHARE}"]
    assert metrics["quality_claim_ai"] == "NOT_AVAILABLE"
    assert metrics["machine_vs_ai"]["accuracy"] == 0.836  # 51/61(按今日标签重述)
    assert metrics["machine_vs_ai"]["verdict_as_recorded"] == {"通过": 46, "驳回": 15}
    assert "≠ 人工达标" in metrics["caliber_note"]


def test_harness_still_exits_2_with_ai_sidecar_present():
    """61 条 AI 裁定在手,退出码仍是 2:AI 初审不构成质量达标。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "golden_v2_harness.py"), "--no-report"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 2, proc.stdout[-800:] + proc.stderr[-800:]


def test_harness_rejects_human_fields_leaked_into_sidecar(tmp_path, cards):
    cards_path = tmp_path / "cards.jsonl"
    cards_path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cards) + "\n", encoding="utf-8")
    side = tmp_path / "cards_ai_adjudication.jsonl"
    row = dict(_read_jsonl(SIDECAR_PATH)[0])
    row["human_verdict"] = "通过"          # 冒充人工 —— 必须被拦
    side.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    ai, errors = harness.load_ai_adjudications(side, {c["card_id"] for c in cards})
    assert errors and "human_" in "".join(errors)
    assert ai  # 记录仍返回,但错误必须让 main 退出 1


def test_harness_rejects_unknown_duplicate_and_out_of_range(tmp_path, cards):
    rows = []
    row = dict(_read_jsonl(SIDECAR_PATH)[0])
    rows.append(row)
    rows.append(dict(row))                                     # 重复 card_id
    bad = dict(row)
    bad["card_id"] = "v2zz9"
    rows.append(bad)                                           # 卡片文件里没有
    bad2 = dict(_read_jsonl(SIDECAR_PATH)[1])
    bad2["ai_verdict"] = "大体上对"
    rows.append(bad2)                                          # 取值越界
    side = tmp_path / "x_ai_adjudication.jsonl"
    side.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    _, errors = harness.load_ai_adjudications(side, {c["card_id"] for c in cards})
    joined = "".join(errors)
    assert "重复" in joined and "不存在" in joined and "不在允许集合" in joined


def test_default_sidecar_lookup_is_scoped_to_the_cards_file(tmp_path):
    """临时夹具不会误吃到仓库里的 61 条 AI 裁定(否则假样本会被 AI 口径喂绿)。"""
    path = harness.ai_sidecar_path_for(tmp_path / "cards.jsonl")
    assert path == tmp_path / "cards_ai_adjudication.jsonl"
    ai, errors = harness.load_ai_adjudications(path, set())
    assert ai == {} and errors == []                            # 没有 sidecar ≠ 错误


# ---------------------------------------------------------------- 3. T-03 错标签回归

# 5 张「句子造了假、当年却被放行或贴错标签」的卡,T-03(2026-11-17)已修。
# 钉在这里,防止以后改回去:每张都必须拦下,并给出具体缺哪个维度。
T03_REGRESSION = {
    "v2t13": ("negative_perturbed", lambda m: "numbers:10" in m["missing_dims"]),
    "v2t14": ("negative_perturbed", lambda m: any(d.startswith("dates:3126") for d in m["missing_dims"])
              and any(d.startswith("numbers:3126") for d in m["missing_dims"])),
    "v2h02": ("negative_fabricated_causal", lambda m: "causal:导致" in m["missing_dims"]),
    "v2h03": ("negative_wrong_entity", lambda m: any(d.startswith("persons:") for d in m["missing_dims"])),
    "v2h16": ("unrelated", lambda m: m["quote_verbatim_ok"] is False),
}


@pytest.mark.parametrize("card_id", sorted(T03_REGRESSION))
def test_t03_mislabelled_cards_are_now_blocked(cards, card_id):
    card = next(c for c in cards if c["card_id"] == card_id)
    assert card["kind"] == T03_REGRESSION[card_id][0]
    m = harness.machine_result(card)
    assert m["credibility"] == "unverified", (card_id, m["credibility"])
    assert m["credibility"] != "verified", f"{card_id}:造假句又被放行了"
    assert m["credibility"] != "opinion", f"{card_id}:造假句逃进「转述」通道"
    assert T03_REGRESSION[card_id][1](m), f"{card_id}:没给出应有的缺失维度:{m['missing_dims']}"
    # 构造真值与机器结论现在一致
    assert card["expected_by_construction"] == m["credibility"]


def test_positive_card_v2h01_is_verified_not_routed_as_opinion(cards):
    """v2h01 是正样本:原句直接支撑整句话,当年却被贴成「转述」。T-03 之后必须能核实。"""
    card = next(c for c in cards if c["card_id"] == "v2h01")
    m = harness.machine_result(card)
    assert card["kind"] == "positive"
    assert m["credibility"] == "verified"
    assert m["missing_dims"] == []


def test_gate_critical_findings_fire_for_the_regression_cards(cards):
    """回归卡必须被写入门禁识别为 critical —— 不只是贴个标签,是真的拦下来不发。

    用真的门禁函数 `verifier.deterministic_findings`,不复刻它的逻辑。
    """
    from ttxt.writing import verifier

    for card_id in sorted(T03_REGRESSION):
        card = next(c for c in cards if c["card_id"] == card_id)
        article = {
            "id": card.get("source_article_id"),
            "title": card.get("source_title") or "",
            "url": card.get("source_url") or "",
            "text": card.get("source_text") or "",
        }
        evidence = verifier.verify_claim(card["claim"], article)
        assert evidence["kind"] == "fact", (card_id, evidence["kind"])
        _issues, critical = verifier.deterministic_findings([evidence], card["claim"], card["claim"])
        assert critical, f"{card_id}:门禁没有 critical,等于放行"
        assert any(item.startswith("事实未通过来源核验") for item in critical), (card_id, critical)


# ---------------------------------------------------------------- 4. T-15 已知缺陷登记


def test_ten_opinion_routed_cards_still_escape_the_fact_gate(cards, sidecar):
    """登记 T-15 现状:10 张伪造「业内人士…」的卡走 opinion 通道,门禁不跑事实核验。

    这是**已知未修**的缺陷,不是这条测试造成的。断言的是当前行为,
    将来修好路由器(见 T-15)时这条会红,提醒同步更新答案钥匙与 A/B 口径。
    """
    routed = sorted(c["card_id"] for c in cards if c["kind"] == "opinion_attributed")
    assert len(routed) == 10
    from ttxt.writing import verifier

    ai = {r["card_id"]: r for r in sidecar}
    for card_id in routed:
        card = next(c for c in cards if c["card_id"] == card_id)
        m = harness.machine_result(card)
        assert m["credibility"] == "opinion", (card_id, m["credibility"])
        assert m["missing_dims"] == [], f"{card_id}:走 opinion 通道本该什么都不核验"
        # 门禁层面:这类卡连事实核验都不进,一条 critical 都不产生 —— 缺陷的现状
        article = {
            "id": card.get("source_article_id"),
            "title": card.get("source_title") or "",
            "url": card.get("source_url") or "",
            "text": card.get("source_text") or "",
        }
        evidence = verifier.verify_claim(card["claim"], article)
        assert evidence["kind"] == "opinion"
        _issues, critical = verifier.deterministic_findings([evidence], card["claim"], card["claim"])
        assert critical == [], (
            f"{card_id}:伪造归属句现在被门禁拦下了 —— 说明 T-15 已修,"
            "同步更新答案钥匙、A/B 口径和这条断言")
        # AI 初审 10/10 认为机器贴得不贴切(应判「对不上」),已作为 T-15 的证据
        assert ai[card_id]["ai_verdict"] == "驳回"
        assert any(k in ai[card_id]["ai_reason"] for k in ("转述", "归属", "归因")), ai[card_id]["ai_reason"]


def test_answer_key_is_untouched_by_the_ai_pass(cards, sidecar):
    """AI 判机器错,不改答案钥匙:expected_by_construction 仍与机器结论一致(61/61)。

    改答案钥匙会让 A/B 口径失真,所以 AI 的不同意见只进 sidecar,等 T-15 用生产证据处理。
    """
    assert all(c["expected_by_construction"] == harness.machine_result(c)["credibility"] for c in cards)
    disagree = [r["card_id"] for r in sidecar if r["ai_verdict_current"] == "驳回"]
    assert len(disagree) == 10
    assert set(disagree) == {c["card_id"] for c in cards if c["kind"] == "opinion_attributed"}
