"""R53:绑定纪律的定点修复(targeted repair)——把"生成时绑错锚点"从整篇作废变成定点修复。

口径(与账本 R53 段一致):
- 草稿完成后逐条 claim 跑校验(复用 verifier.verify_claim 的锚点抽取与
  _anchor_in/_date_in/引句 ±1 句窗口判定,不另造判定逻辑);
- 失败 claim 定位到草稿原文那一句,**只重写该句**:请求里带上它原本应绑的来源引句
  (绑定引句 ±1 句窗口;锚点在材料池别的文章里找到时,附那篇的原句候选),上下文不动;
- 每条 claim 最多修 2 轮;改完立刻用同一 verify_claim 复验该句,通过才替换进正文
  与证据行;两轮仍失败 ⇒ 如实判失败(证据行原样保留、照进 critical 与可核率统计),
  并记录失败形状(脑补/绑错文章/移花接木/未绑定/无来源/伪造归属);
- 门禁一个字不动:修复不碰阈值、不跳过校验、不把失败 claim 剔出统计;
  复验不带模型自报引句(只走自动绑定),判定只会比生成链路更严,不会更松;
- 修复重写不计入门禁修订计数 revisions(与 R48 去模板化定向重写同一先例),
  草稿变化另存新版本,版本账目可审。

度量(值不值得留在链路就看这三个数):
- 修复成功率:fixed_first / fixed_second / unfixed 各几例;
- 每条 claim 平均模型调用增量:model_calls ÷ claims;
- 单篇耗时增量:探针 run 级 elapsed 对比(不在本模块记账)。
"""

from __future__ import annotations

from ttxt.crawler.simhash import tokenize
from ttxt.writing import prompts, verifier

MAX_REPAIR_ROUNDS = 2
REPAIR_MAX_TOKENS = 320
REPAIR_TEMPERATURE = 0.2
LOCATE_MIN_OVERLAP = 0.5  # 定位回退(词面重叠)的接受线:低于此宁可记 not_located
MAX_NEW_SENTENCE_CHARS = 300
_SOURCE_WINDOW_CHARS = 500
_CANDIDATE_QUOTE_CHARS = 200
_CANDIDATE_LIMIT = 3


def new_stats() -> dict:
    return {
        "claims": 0,
        "facts_failed": 0,
        "located": 0,
        "not_located": 0,
        "attempts": 0,
        "model_calls": 0,
        "invalid_outputs": 0,
        "anchor_erased": 0,
        "fixed_first": 0,
        "fixed_second": 0,
        "unfixed": 0,
        "rebound": 0,
        "changed_sentences": 0,
        "errors": 0,
        "unfixed_shapes": {},
        "details": [],
    }


def merge_stats(total: dict, part: dict) -> dict:
    """把同一 run 内多个版本(初稿 + 各修订版)的修复读数累加进 run 级总账。"""
    for key in (
        "claims", "facts_failed", "located", "not_located", "attempts", "model_calls",
        "invalid_outputs", "anchor_erased", "fixed_first", "fixed_second", "unfixed", "rebound",
        "changed_sentences", "errors",
    ):
        total[key] += part.get(key, 0)
    for shape, n in (part.get("unfixed_shapes") or {}).items():
        total["unfixed_shapes"][shape] = total["unfixed_shapes"].get(shape, 0) + n
    total["details"].extend(part.get("details") or [])
    return total


def locate_sentence(draft: str, claim: str) -> tuple[int, int] | None:
    """把 claim 定位回草稿句子:先逐字 find;失败再按词面重叠取最相近的一句。

    这是修复层的定位工具,不是门禁判定:重叠低于 LOCATE_MIN_OVERLAP 一律记
    not_located(宁可放弃修复,也不在错误的句子上动刀)。
    """
    claim = (claim or "").strip()
    if not claim:
        return None
    idx = draft.find(claim)
    if idx >= 0:
        return idx, idx + len(claim)
    claim_tokens = set(tokenize(claim))
    if not claim_tokens:
        return None
    best: tuple[int, int, float] | None = None
    for chunk, offset in verifier._source_slices(draft):
        tokens = set(tokenize(chunk))
        if not tokens:
            continue
        overlap = len(claim_tokens & tokens) / len(claim_tokens)
        if best is None or overlap > best[2]:
            best = (offset, offset + len(chunk), overlap)
    if best and best[2] >= LOCATE_MIN_OVERLAP:
        return best[0], best[1]
    return None


def _missing_anchors(row: dict) -> list[str]:
    checks = row.get("checks") or {}
    missing: list[str] = []
    for dim in ("numbers", "dates", "persons", "orgs", "causal"):
        payload = checks.get(dim) or {}
        if isinstance(payload, dict):
            missing += payload.get("missing") or []
    return missing


def _other_material_hits(row: dict, materials: list[dict]) -> dict[str, list[dict]]:
    """claim 缺的锚点在整个材料池(绑定文章以外)哪里出现过——只做诊断与修复素材,
    不改变任何判定:是否放行仍由 verify_claim 对最终句子的核验说了算。"""
    bound_id = row.get("article_id")
    out: dict[str, list[dict]] = {}
    for item in _missing_anchors(row):
        needle = verifier.norm(item)
        hits = []
        for mat in materials:
            if mat.get("id") == bound_id:
                continue
            text = mat.get("text") or ""
            if not text or not verifier._anchor_in(needle, verifier.norm(text)):
                continue
            sentence = next(
                (
                    chunk
                    for chunk, _ in verifier._source_slices(text)
                    if verifier._anchor_in(needle, verifier.norm(chunk))
                ),
                "",
            )
            hits.append({"id": mat.get("id"), "title": mat.get("title"), "sentence": sentence[:_CANDIDATE_QUOTE_CHARS]})
        out[item] = hits
    return out


def failure_shape(row: dict, other_hits: dict[str, list[dict]]) -> str:
    """失败形状归类(如实记录用,不参与放行):
    - 无来源/伪造归属/引句未绑定/移花接木 直接由证据行字段可见;
    - 绑错文章:锚点缺失但在材料池别处出现;
    - 脑补:锚点在整个材料池都找不到。
    """
    checks = row.get("checks") or {}
    if row.get("credibility") == "no_source" or not row.get("article_id"):
        return "no_source(未绑定有效来源)"
    if (checks.get("attribution") or {}).get("missing"):
        return "attribution_fabricated(伪造归属)"
    if row.get("quote") is None:
        return "quote_unbound(引句未绑定)"
    if any(key.endswith("_outside_quote") and checks.get(key) for key in checks):
        return "grafted(锚点在引句窗口外·移花接木)"
    if any(hits for hits in other_hits.values()):
        return "wrong_binding(绑错文章·锚点在池内别处)"
    return "fabricated(锚点全池找不到·脑补)"


def _sanitize(output: str) -> str:
    """模型改写输出取首行非空,剥外层引号与句末标点(替换区间不含标点,防止双句号)。"""
    line = next((ln.strip() for ln in (output or "").splitlines() if ln.strip()), "")
    line = line.strip("「」『』\"“”'").strip()
    line = line.rstrip("。！？!?；;，,")
    if not line or len(line) > MAX_NEW_SENTENCE_CHARS:
        return ""
    return line


def _has_fact_anchor(text: str) -> bool:
    anchors = verifier.extract_anchors(text)
    return any(anchors[key] for key in ("numbers", "dates", "persons", "orgs", "causal"))


def _source_block(row: dict, article: dict | None) -> str:
    """它原本应绑的来源:绑定引句 ±1 句连续窗口(与门禁同一把尺子的窗口)。"""
    text = (article or {}).get("text") or ""
    if not text:
        return "(绑定来源为空)"
    if row.get("quote"):
        window, _off = verifier.anchor_window(text, row["quote"], row.get("quote_offset") or 0)
    else:
        window = text
    return window[:_SOURCE_WINDOW_CHARS]


def repair_failing_claims(
    model_call, draft: str, evidence: list[dict], materials: list[dict], *, enable_thinking: bool = False
) -> tuple[str, list[dict], dict]:
    """逐条失败 claim 定点重写 + 即时复验。返回 (新草稿, 新证据行, 修复读数)。

    只有通过复验(verified/opinion)的句子才会替换进草稿与证据行;
    修不好的句子原文原证据一律不动,失败形状记入 stats。
    """
    stats = new_stats()
    stats["claims"] = len(evidence)
    evidence = list(evidence)
    for index, row in enumerate(evidence):
        if row.get("kind") != "fact" or row.get("credibility") == "verified":
            continue
        stats["facts_failed"] += 1
        other_hits = _other_material_hits(row, materials)
        shape = failure_shape(row, other_hits)
        loc = locate_sentence(draft, row.get("claim") or "")
        if loc is None:
            stats["not_located"] += 1
            stats["details"].append({"claim": (row.get("claim") or "")[:60], "shape": shape, "outcome": "not_located", "attempts": 0})
            continue
        stats["located"] += 1
        start, end = loc
        old_sentence = draft[start:end].strip()
        article = next((m for m in materials if m.get("id") == row.get("article_id")), None)
        # 候选重绑材料:缺的锚点在池内别处出现过的文章(复验时逐一试,试不过就是修不好)
        candidates: list[dict] = []
        for hits in other_hits.values():
            for hit in hits:
                mat = next((m for m in materials if m.get("id") == hit["id"]), None)
                if mat is not None and all(c.get("id") != mat.get("id") for c in candidates):
                    candidates.append(mat)
        candidate_sentences = [
            hit["sentence"]
            for hits in other_hits.values()
            for hit in hits[:1]
        ]
        if row.get("credibility") == "no_source":
            # 无来源行没有锚点线索:全池都是候选(复验仍走 verify_claim,不放水)
            candidates = [m for m in materials if m.get("id") != row.get("article_id")]
            candidate_sentences = [
                (verifier.anchor_window(m.get("text") or "", "", 0)[0] or "")[:_CANDIDATE_QUOTE_CHARS]
                for m in candidates[:_CANDIDATE_LIMIT]
            ]
        candidate_lines = [
            f"[材料{mat.get('id')}|{mat.get('title')}] {sentence}"
            for mat, sentence in zip(candidates[:_CANDIDATE_LIMIT], candidate_sentences[:_CANDIDATE_LIMIT])
        ]
        # 门禁同款 critical 文案作为失败详情(_missing_anchors 只是补充锚点清单)
        failure = ";".join(verifier.deterministic_findings([row], "", "")[1]) or shape
        fixed = False
        for attempt in range(1, MAX_REPAIR_ROUNDS + 1):
            try:
                output = model_call(
                    prompts.repair_sentence_prompt(
                        draft=draft,
                        target=old_sentence,
                        failure=failure,
                        missing=",".join(_missing_anchors(row)) or "(见失败详情)",
                        source_block=_source_block(row, article),
                        candidate_lines=candidate_lines,
                    ),
                    max_tokens=REPAIR_MAX_TOKENS,
                    temperature=REPAIR_TEMPERATURE,
                    enable_thinking=enable_thinking,
                )
            except Exception as exc:  # noqa: BLE001 - 通道异常如实记账,不静默
                stats["model_calls"] += 1
                stats["attempts"] += 1
                stats["errors"] += 1
                stats["details"].append({"claim": old_sentence[:60], "shape": shape, "outcome": f"model_error:{str(exc)[:120]}", "attempts": attempt})
                continue
            stats["model_calls"] += 1
            stats["attempts"] += 1
            new_sentence = _sanitize(output)
            if not new_sentence:
                stats["invalid_outputs"] += 1
                continue
            # 洗掉锚点的空话不得借复验过关:原句带事实锚点、新句五维锚点全空 ⇒ 拒收。
            # (这比门禁更严:门禁只按整篇 evidence_empty,修复层逐句堵"删事实换空话"。)
            if _has_fact_anchor(old_sentence) and not _has_fact_anchor(new_sentence):
                stats["anchor_erased"] += 1
                continue
            # 输出与原文相同也允许:绑错文章时,正确的修复就是"句子不动、换绑来源",
            # 是否成立由复验裁决(原句重绑仍过不了 verify_claim 就是修不好)。
            # 复验:与原绑定文章 + 锚点候选文章逐一走 verify_claim(不带模型自报引句,只走自动绑定)
            verified_row = None
            for art in [article] + candidates:
                if art is None:
                    continue
                cand = verifier.verify_claim(new_sentence, art)
                if cand.get("credibility") in ("verified", "opinion"):
                    verified_row = cand
                    break
            if verified_row is None:
                continue
            draft = draft[:start] + new_sentence + draft[end:]
            evidence[index] = verified_row
            stats["fixed_first" if attempt == 1 else "fixed_second"] += 1
            stats["changed_sentences"] += 1
            if verified_row.get("article_id") != row.get("article_id"):
                stats["rebound"] += 1
            stats["details"].append({"claim": old_sentence[:60], "shape": shape, "outcome": f"fixed_round_{attempt}", "attempts": attempt, "new_article_id": verified_row.get("article_id")})
            fixed = True
            break
        if not fixed:
            stats["unfixed"] += 1
            stats["unfixed_shapes"][shape] = stats["unfixed_shapes"].get(shape, 0) + 1
            stats["details"].append({"claim": old_sentence[:60], "shape": shape, "outcome": "unfixed", "attempts": MAX_REPAIR_ROUNDS})
    return draft, evidence, stats
