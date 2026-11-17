"""P13 批次3:AI 辅助初审 sidecar 的护栏 + T-03 错标签回归测试(2026-11-17)。

三件事,各管各的,不许互相顶替:

1. **AI 口径的护栏**:61 张 AI 裁定只许待在
   `10_tests/golden/golden_set_v2_ai_adjudication.jsonl` 的 `ai_*` 字段里。
   卡片文件的 `human_*` 必须仍然全是「未裁定」;sidecar 里出现 `human_*` = 冒充人工 = 模式错误。
   质量门禁(≥50 人工裁定)照旧不过,退出码照旧 2 —— AI 初审不得让 CI 变绿。
2. **T-03 的回归**:2026-11-17 修掉了 6 张「造假句被放行」的卡。这里把
   其中 5 张(第 6 张 v2h01 是正样本,走另一条断言)钉成回归样本,
   验证系统现在**拦得住**这些错误标签,以后改回去就红。
3. **T-15 的登记(2026-11-16 建档,2026-11-17 批次4 已修)**:剩下 10 张 `opinion_attributed`
   卡过去走「转述」通道 —— 伪造的「业内人士预计」把句子送进 opinion,事实门禁根本不跑。
   现在路由器要求第三方归属必须在绑定引句 ±1 句内**逐字**存在
   (`verifier.attribution_check`,详见 `10_tests/test_p13_batch4_attribution_routing.py`),
   这 10 张的机器结论变成 `unverified`,答案钥匙也按构造真值迁到了 `unverified`
   (`scripts/golden_v2_fix_t15_answer_key.py`)。
   代价如实登记:2026-11-16 那份按纸面标签「转述」做的 AI 裁定,对这 10 张**判据已过期**
   ⇒ harness 的 AI 口径多出一条门禁未满足项,生成器拒绝重新落盘(见下面两条测试)。
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

# T-15 的 10 张伪造归属卡(kind = opinion_attributed)。2026-11-17 批次4 修好路由器之后,
# 它们的机器结论从 opinion(转述)变成 unverified(对不上),AI sidecar 里记的机器标签
# 因此整体过期 —— 这是修复的**代价**,不是 bug。
T15_CARDS = ["v2f06", "v2f12", "v2f17", "v2h04", "v2h09", "v2h15",
             "v2t05", "v2t11", "v2t17", "v2t23"]


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
    """sidecar 抄的机器标签必须跟今天的机器结论一致;不一致就是判据过期,得重裁。

    T-15 修复(批次4,2026-11-17)把 10 张伪造归属卡的机器结论从「转述」改成「对不上」:
    2026-11-16 抄下的机器标签对**恰好这 10 张**已过期,必须重裁,不得沿用。
    修复前这条钉的是 `drift == []`(当时的现状);现在钉的是「只漂这 10 张,别的一张都不许漂」。
    """
    drift = sorted(r["card_id"] for r in sidecar if r["machine_now"] != machine_now[r["card_id"]])
    assert drift == T15_CARDS, f"机器标签漂移的卡片不是 T-15 那 10 张:{drift}"
    for row in sidecar:
        if row["card_id"] in T15_CARDS:
            assert row["machine_now"] == "opinion", row["card_id"]      # 当时机器贴的是「转述」
            assert machine_now[row["card_id"]] == "unverified", row["card_id"]   # 今天机器判「对不上」


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


def test_generator_refuses_to_regenerate_the_sidecar_after_t15(tmp_path, cards):
    """T-15 修复前:重跑生成器逐字复现 sidecar(只允许录入时间不同)。
    T-15 修复后:10 张伪造归属卡的今日机器结论已变,生成器必须**拒绝**重新落盘,
    而不是把 2026-11-16 按「转述」做的旧判定悄悄沿用一遍。
    这条断言变红 = 要么 AI 裁定重做完了(判据重新对齐),要么护栏被削弱。
    """
    out = tmp_path / "again.jsonl"
    assert generator.main(["--out", str(out)]) == 1
    assert not out.exists(), "判据已过期却仍然写出裁定表 = 沿用旧判定"
    sheet = generator.load_sheet_tokens(SHEET_TOKENS_PATH)
    machine = {c["card_id"]: harness.machine_result(c)["credibility"] for c in cards}
    _rows, errors = generator.build_rows(cards, machine, sheet)
    assert sorted({e.split(":")[0] for e in errors}) == T15_CARDS, errors
    assert len(errors) == 20, errors                    # 每张两条:补重述理由 + 重判不得引用旧标签
    assert all(("重述" in e and "不能沿用旧判定" in e) or "重判所引标签" in e for e in errors), errors


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
    """重判后人工口径仍是 0、质量结论仍 NOT_AVAILABLE、退出码仍 2。
    T-15 修复后 AI 口径多出一条未满足项:10 张卡的机器标签变了 ⇒ 判据过期必须重裁。
    """
    metrics = harness.evaluate(cards, ai)
    assert metrics["human_adjudicated_count"] == 0
    assert metrics["quality_claim"] == "NOT_AVAILABLE"
    assert metrics["quality_claim_ai"] == "NOT_AVAILABLE"
    assert metrics["ai_gate_failures"][0] == "AI 确认的 A/B 级独立证据占比 0.279 < 0.5"
    assert len(metrics["ai_gate_failures"]) == 2, metrics["ai_gate_failures"]
    drift_line = metrics["ai_gate_failures"][1]
    assert "机器标签与当前机器结论不一致 10 张" in drift_line and "必须重裁" in drift_line
    assert all(cid in drift_line for cid in T15_CARDS)


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
    # AI 口径:数量够了,但 A/B 独立证据占比仍卡在 17/61 —— T-02 未解;
    # T-15 修好之后又多一条:10 张卡的机器标签变了,AI 判据过期必须重裁(门禁只会变严不会变松)
    assert metrics["ai_gate_failures"][0] == \
        f"AI 确认的 A/B 级独立证据占比 0.279 < {harness.MIN_INDEPENDENT_SHARE}"
    assert len(metrics["ai_gate_failures"]) == 2, metrics["ai_gate_failures"]
    assert metrics["machine_vs_ai"]["machine_label_drift"] == T15_CARDS
    assert metrics["machine_vs_ai"]["basis_stale_cards"] == [
        "v2h01", "v2h02", "v2h03", "v2h16", "v2t13", "v2t14"]   # 纸面标签口径没变,还是 T-03 那 6 张
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


def test_ten_fabricated_attribution_cards_now_hit_the_fact_gate(cards, sidecar):
    """T-15 已修(批次4,2026-11-17):10 张伪造「业内人士…」的卡不再走转述通道。

    修复前这条测试登记的是缺陷现状(credibility=opinion、missing_dims=[]、一条 critical 都不产);
    路由器现在要求第三方归属必须在绑定引句 ±1 句内逐字存在,断言随之钉成反面:
    进门禁、有 critical、missing_dims 至少含 attribution。细节断言在
    `10_tests/test_p13_batch4_attribution_routing.py`,这里钉答案钥匙与 AI 证据的一致性。
    """
    from ttxt.writing import verifier

    routed = sorted(c["card_id"] for c in cards if c["kind"] == "opinion_attributed")
    assert routed == T15_CARDS
    ai = {r["card_id"]: r for r in sidecar}
    for card_id in routed:
        card = next(c for c in cards if c["card_id"] == card_id)
        m = harness.machine_result(card)
        assert m["credibility"] == "unverified", (card_id, m["credibility"])
        assert any(dim.startswith("attribution:") for dim in m["missing_dims"]), (card_id, m["missing_dims"])
        article = {
            "id": card.get("source_article_id"),
            "title": card.get("source_title") or "",
            "url": card.get("source_url") or "",
            "text": card.get("source_text") or "",
        }
        evidence = verifier.verify_claim(card["claim"], article)
        assert evidence["kind"] == "fact", (card_id, evidence["kind"])
        _issues, critical = verifier.deterministic_findings([evidence], card["claim"], card["claim"])
        assert any("attribution_not_in_source" in item for item in critical), (card_id, critical)
        assert any(item.startswith("事实未通过来源核验") for item in critical), (card_id, critical)
        # 答案钥匙跟着现实走:这 10 张的构造真值本来就是「归属无出处、事实未核实」
        assert card["expected_by_construction"] == "unverified" == m["credibility"], card_id
        # AI 初审 10/10 认为机器当时贴「转述」不贴切(应判「对不上」)—— 与今天的机器结论一致
        assert ai[card_id]["ai_verdict"] == "驳回"
        assert any(k in ai[card_id]["ai_reason"] for k in ("转述", "归属", "归因")), ai[card_id]["ai_reason"]


def test_answer_key_now_matches_the_machine_for_all_61_cards(cards, sidecar):
    """答案钥匙只被 T-15 那 10 行动过(其余 51 行仍是构造时的原值),61/61 与机器结论一致。

    AI 的不同意见本身不改答案钥匙:它只进 sidecar;改钥匙的是 T-15 的路由器修复
    (机器结论与构造真值同时变成 unverified),由 `scripts/golden_v2_fix_t15_answer_key.py` 落盘。
    """
    assert all(c["expected_by_construction"] == harness.machine_result(c)["credibility"] for c in cards)
    moved = [c["card_id"] for c in cards if c["kind"] == "opinion_attributed"]
    assert sorted(moved) == T15_CARDS
    assert all(c["expected_by_construction"] == "unverified" for c in cards if c["card_id"] in T15_CARDS)
    assert sum(1 for c in cards if c["expected_by_construction"] == "opinion") == 0
    disagree = [r["card_id"] for r in sidecar if r["ai_verdict_current"] == "驳回"]
    assert len(disagree) == 10
    assert set(disagree) == set(T15_CARDS)
