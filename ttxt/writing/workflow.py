"""写作工作流(含 L4 质量门禁)。

门禁规则:
- 完整性:finish_reason=length 自动加大 token 重试一次;文本启发式检查
  (长度 ≥ gate_min_chars 且末行句末标点收尾);
- 评审:{score, issues, critical},critical 含事实错误/编造/无来源断言/截断;
- 硬门禁:完整性 OK 且 score ≥ gate_min_score 且 critical 为空 → passed,
  否则修订(≤ gate_max_revisions);修订用尽仍未过 → rejected,
  仅导出到 drafts/<类目>/rejected/,成品目录不得出现。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from ttxt import config as cfg
from ttxt.analysis import keywords, rag, viral
from ttxt.crawler import cleaner, store
from ttxt.db import connect
from ttxt.logging import get_logger
from ttxt.model_gate import client as model_client
from ttxt.model_gate import gate

from . import export, prompts, review as review_channel, verifier

LOG = get_logger("writing")
MAX_CLAIMS = 12
# 逐字引句版 claims(最多 12 条 claim+quote)远超旧值 400;生产实测 JSON 被截断 ⇒
# parse_claims 返回空数组 ⇒ 证据链整段为空。给足预算,由 token 上限而不是沉默兜底。
CLAIMS_MAX_TOKENS = 1200
# 取材:BM25 先取 15 篇候选池,重排后只喂 5 篇进提示词(提示词体积不变,选的是最合适的 5 篇)
MATERIAL_POOL_LIMIT = 15
MATERIAL_TOP_K = 5
CRITICAL_HINTS = ("事实", "编造", "虚假", "来源", "截断", "夸大", "无依据", "数据错误")
TERMINAL_CHARS = "。！？!?…\"”'』」"


def default_model_call(
    prompt: str,
    max_tokens: int = 800,
    temperature: float = 0.3,
    enable_thinking: bool = False,
) -> str:
    """经 model_gate 串行调用;finish_reason=length 时自动加大 token 重试一次。"""

    def _call(tokens: int) -> dict:
        outcome = gate.submit_and_run(
            "writing",
            {
                "prompt": prompt,
                "max_tokens": tokens,
                "temperature": temperature,
                "enable_thinking": enable_thinking,
            },
        )
        done = [
            item
            for item in outcome.get("results", [])
            if item.get("outcome") == "done"
            and isinstance(item.get("result"), dict)
            and item["result"].get("text")
        ]
        if not done:
            error = "no result"
            if outcome.get("results"):
                error = outcome["results"][-1].get("result", {}).get("error", "no result")
            raise RuntimeError(f"model task failed: {error}")
        return done[-1]["result"]

    result = _call(max_tokens)
    if result.get("finish_reason") == "length":
        bigger = min(int(max_tokens * 1.5), int(cfg.load_config().get("writing", {}).get("draft_retry_max_tokens", 2048)))
        if bigger > max_tokens:
            LOG.warn("writing.truncation_retry", max_tokens=max_tokens, retry_tokens=bigger)
            result = _call(bigger)
    return result["text"]


def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def parse_claims(text: str) -> list[dict]:
    cleaned = _strip_fences(text)
    candidates: list = []
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            candidates = data
    except Exception:  # noqa: BLE001 - 容错解析
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, list):
                    candidates = data
            except Exception:  # noqa: BLE001
                candidates = []
    claims: list[dict] = []
    for item in candidates[:MAX_CLAIMS]:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        if not claim:
            continue
        try:
            article_id = int(item.get("article_id") or 0)
        except (TypeError, ValueError):
            article_id = 0
        parsed = {"claim": claim, "article_id": article_id}
        # 模型自报的逐字引句(可选):留下后由 verifier 逐字校验,非逐字一律不采信。
        quote = str(item.get("quote") or "").strip()
        if quote:
            parsed["quote"] = quote
        claims.append(parsed)
    return claims


def parse_review(text: str) -> dict:
    cleaned = _strip_fences(text)
    payload: dict | None = None
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            payload = data
    except Exception:  # noqa: BLE001
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict):
                    payload = data
            except Exception:  # noqa: BLE001
                payload = None
    score: float | None = None
    issues: list[str] = []
    critical: list[str] = []
    if payload:
        try:
            score = float(payload.get("score"))
        except (TypeError, ValueError):
            score = None
        raw_issues = payload.get("issues") or []
        if isinstance(raw_issues, list):
            issues = [str(item).strip() for item in raw_issues if str(item).strip()]
        elif isinstance(raw_issues, str) and raw_issues.strip():
            issues = [raw_issues.strip()]
        raw_critical = payload.get("critical") or []
        if isinstance(raw_critical, list):
            critical = [str(item).strip() for item in raw_critical if str(item).strip()]
        elif isinstance(raw_critical, str) and raw_critical.strip():
            critical = [raw_critical.strip()]
    if not critical:
        critical = [issue for issue in issues if any(hint in issue for hint in CRITICAL_HINTS)]
    if score is None:
        match = re.search(r"(?:score|评分)\D{0,4}(0(?:\.\d+)?|1(?:\.0+)?)", cleaned)
        if match:
            score = float(match.group(1))
    if score is not None:
        score = max(0.0, min(1.0, score))
    return {"score": score, "issues": issues, "critical": critical, "raw": cleaned[:2000]}


def build_materials(materials: list[dict]) -> str:
    if not materials:
        return "（无可用参考材料;请避免陈述具体事实,无法确认处写“待核实”）"
    lines = []
    for index, material in enumerate(materials, start=1):
        snippet = (material["text"] or "").replace("\n", " ")[:240]
        lines.append(f"[{index}] {material['title']} — {snippet}")
    return "\n".join(lines)


def verify_claims(claims: list[dict], materials: list[dict]) -> list[dict]:
    """批次4核验:逐事实绑定 URL + 原文连续引句 + 分维度锚点校验。

    credibility 语义(P12 起):verified / unverified / no_source / opinion;
    旧语义 supported/weak 已废弃——overlap 只是引句绑定的副产物,
    不再是可信度判据。
    """
    evidence: list[dict] = []
    for item in claims:
        number = int(item.get("article_id") or 0)
        article = materials[number - 1] if 1 <= number <= len(materials) else None
        evidence.append(verifier.verify_claim(item["claim"], article, quote=item.get("quote")))
    return evidence


def _new_draft_id(category: str, topic: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    digest = hashlib.sha1(f"{category}|{topic}|{stamp}".encode("utf-8")).hexdigest()[:6]
    return f"{category}-{stamp}-{digest}"


def _save_version(
    db_path: Path,
    draft_id: str,
    category: str,
    topic: str,
    version: int,
    content: str,
    model_id: str | None,
) -> None:
    con = connect(db_path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO writing_history(draft_id, category, topic, version, content, model_id, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (draft_id, category, topic, version, content, model_id, "draft", cfg.now_iso_ms()),
        )
        con.commit()
    finally:
        con.close()


def _set_draft_status(db_path: Path, draft_id: str, version: int, status: str) -> None:
    """按**版本**回写门禁结论,不得把最终状态刷到所有历史版本上。

    旧实现 `WHERE draft_id=?` 让同一个草稿的所有版本行都变成最终状态:
    v1 明明被门禁拦下,却因 v2 通过而显示 passed(P13 报告实测 13 行全 passed、
    全表 0 行 rejected,版本账目失真)。循环语义本身就说明:比当前版本更早的版本
    都在门禁上失败过,因此它们的状态是 rejected。

    状态口径(账本 T-05)是 ``REJECTED / AUTO_PASSED / INDEPENDENT_REVIEW_PASSED``;
    导出目录与 CLI 回执仍用 ``passed/rejected``(落盘目录名不变)。2026-11-18 之前的
    旧行是小写口径,不回刷——历史行只描述当时写下的事实。
    """
    con = connect(db_path)
    try:
        con.execute(
            "UPDATE writing_history SET status = CASE WHEN version < ? THEN 'REJECTED' ELSE ? END "
            "WHERE draft_id = ?",
            (version, status, draft_id),
        )
        con.commit()
    finally:
        con.close()


def _save_evaluation(
    db_path: Path,
    draft_id: str,
    version: int,
    review: dict,
    meta: dict | None = None,
    evaluator: str | None = None,
) -> None:
    con = connect(db_path)
    try:
        con.execute(
            "INSERT INTO evaluation_history(draft_id, version, evaluator, criteria_json, score, issues_json, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                draft_id,
                version,
                # evaluator 必须是真的提供评审的模型 id,不能笼统写 "model_review"(账本 T-04/T-05 要能追责到模型)
                evaluator or "model_review",
                json.dumps({"criteria": "facts/structure/title/plain", **(meta or {})}, ensure_ascii=False),
                review.get("score"),
                json.dumps(
                    {"issues": review.get("issues") or [], "critical": review.get("critical") or []},
                    ensure_ascii=False,
                ),
                cfg.now_iso_ms(),
            ),
        )
        con.commit()
    finally:
        con.close()


def _save_evidence(db_path: Path, draft_id: str, evidence: list[dict]) -> None:
    con = connect(db_path)
    try:
        con.execute("DELETE FROM evidence_links WHERE draft_id = ?", (draft_id,))
        con.executemany(
            "INSERT INTO evidence_links(draft_id, claim, article_id, quote, overlap, credibility, "
            "source_url, checks_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    draft_id,
                    item["claim"],
                    item.get("article_id"),
                    item.get("quote"),
                    item.get("overlap"),
                    item.get("credibility"),
                    item.get("source_url"),
                    json.dumps(
                        {
                            "kind": item.get("kind"),
                            "quote_offset": item.get("quote_offset"),
                            "anchors": item.get("anchors"),
                            "checks": item.get("checks"),
                        },
                        ensure_ascii=False,
                    ),
                    cfg.now_iso_ms(),
                )
                for item in evidence
            ],
        )
        con.commit()
    finally:
        con.close()


def _describe_evidence_item(item: dict) -> str:
    head = (
        f"- [{item.get('kind') or 'fact'}] {item['claim']}"
        f"（来源:{item.get('article_id') or '无'};URL:{item.get('source_url') or '无'};"
        f"核验:{item['credibility']}）"
    )
    quote = item.get("quote")
    if quote:
        head += f"\n  原文引句(连续切片,offset={item.get('quote_offset')}):{quote[:120]}"
    checks = item.get("checks") or {}
    for dim in ("numbers", "dates", "persons", "orgs", "causal", "attribution"):
        payload = checks.get(dim) or {}
        missing = payload.get("missing") or []
        if missing:
            head += f"\n  维度缺失 {dim}:{','.join(missing)}"
    return head


def _review(model_call, draft: str, evidence: list[dict], enable_thinking: bool) -> dict:
    evidence_text = (
        "\n".join(_describe_evidence_item(item) for item in evidence) or "（无证据）"
    )
    text = model_call(
        prompts.review_prompt(draft, evidence_text),
        max_tokens=500,
        temperature=0.0,
        enable_thinking=enable_thinking,
    )
    return parse_review(text)


def _is_complete(text: str, min_chars: int) -> bool:
    if not text or len(text.strip()) < min_chars:
        return False
    last_line = text.strip().splitlines()[-1].rstrip()
    if not last_line:
        return False
    return last_line[-1] in TERMINAL_CHARS


def _passed(complete: bool, review: dict, min_score: float) -> bool:
    return bool(
        complete
        and review["score"] is not None
        and review["score"] >= min_score
        and not review["critical"]
    )


def _draft_title(draft: str, topic: str) -> tuple[str, str]:
    lines = (draft or "").strip().splitlines()
    title = (lines or [topic])[0][:80]
    body = "\n".join(lines[1:]) if len(lines) > 1 else ""
    return title, body


def _merge_findings(review: dict, issues: list[str], critical: list[str]) -> dict:
    """确定性核验结论并入评审结论(确定性 critical 优先,顺序去重)。"""
    return {
        "score": review.get("score"),
        "issues": list(dict.fromkeys(issues + list(review.get("issues") or []))),
        "critical": list(dict.fromkeys(critical + list(review.get("critical") or []))),
        "raw": review.get("raw", ""),
    }


def _model_meta(model_call) -> dict:
    """写作侧 served_model + 评审独立性。此刻还没有独立评审者,先如实记同源;
    独立评审跑完后由 ``_apply_reviewer`` 按**真实 id 比较**改判(不许为了过门禁写死 false)。"""
    meta = {
        "same_model_review": True,
        "reviewer_model": None,
        "reviewer_limitation": (
            "写作与模型评审由同一服务模型完成,评审独立性受限;"
            "事实门禁另由 verifier 确定性规则独立判定"
        ),
        "served_model": None,
    }
    if model_call is default_model_call:
        try:
            ids = model_client.models()
            meta["served_model"] = ids[0] if ids else None
        except Exception:  # noqa: BLE001 - 探测失败如实记 None
            meta["served_model"] = None
    else:
        meta["note"] = "注入 model_call(测试/离线),未探测 served_model"
    return meta


def _apply_reviewer(meta: dict, outcome: dict | None, error: str | None) -> None:
    """按写作侧与评审侧**真实提供的模型 id** 判定独立性;拿不到证据就继续算同源。"""
    reviewer = (outcome or {}).get("reviewer_model")
    writer = meta.get("served_model")
    independent = bool(reviewer and writer and reviewer != writer)
    meta["reviewer_model"] = reviewer
    meta["same_model_review"] = not independent
    meta["reviewer_limitation"] = (
        f"评审由独立服务模型 {reviewer} 完成,与写作模型 {writer} 不同源"
        if independent
        else "写作与模型评审由同一服务模型完成,评审独立性受限;"
             "事实门禁另由 verifier 确定性规则独立判定"
    )
    if error:
        meta["review_error"] = error
        meta["reviewer_limitation"] += f";独立评审通道不可用:{error}"


def _review_account(passed: bool, meta: dict, outcome: dict | None) -> str:
    """T-05 的版本级状态口径:只有独立评审者真实存在(与写作模型不同 id)且评审自己说可发布,
    才给 INDEPENDENT_REVIEW_PASSED;拿不到许可措辞就不给标签(fail-closed,不猜语义)。"""
    if not passed:
        return "REJECTED"
    verdict = (outcome or {}).get("verdict") or {}
    if outcome and not meta.get("same_model_review") and verdict.get("publishable"):
        return "INDEPENDENT_REVIEW_PASSED"
    return "AUTO_PASSED"


def _review_summary(outcome: dict | None, error: str | None) -> dict:
    """回执里的独立评审摘要:只放可公开字段,评审正文留精简片段(密钥与请求头不进回执)。"""
    if outcome is None:
        return {"attempted": error is not None, "available": False, "error": error}
    return {
        "attempted": True,
        "available": True,
        "reviewer_model": outcome["reviewer_model"],
        "prompt": outcome["prompt"].split("\n\n", 1)[0],
        "verdict_markers": outcome["verdict"],
        "verdict_excerpt": outcome["verdict_text"][:600],
        "finish_reason": outcome.get("finish_reason"),
        "usage": outcome.get("usage"),
        "elapsed_s": outcome.get("elapsed_s"),
    }


def _verified_review(model_call, draft, materials, evidence, enable_thinking, topic):
    """模型评审 + 确定性核验结论合并(标题在此重新绑定)。"""
    review = _review(model_call, draft, evidence, enable_thinking)
    title, body = _draft_title(draft, topic)
    det_issues, det_critical = verifier.deterministic_findings(evidence, title, body)
    return _merge_findings(review, det_issues, det_critical)


def _source_host(url: str | None) -> str:
    """证据密度的计数单位之一:URL 主机(去 ``www.``)。取不到 host 记空串,不假定"有来源"。"""
    netloc = urlsplit(url or "").netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _independence_key(author: str | None, host: str) -> tuple[str, str]:
    """一个"独立来源"的计数键,返回 ``(轴, 键)``。

    生产语料实测**每个类目只有一个域名**(正式类目全是 toutiao.com,health 全是 who.int),
    按域名数独立性会永远等于 1 ⇒ 门禁会把所有写作请求一律拦下(自检见 07_tmp 只读探针)。
    同平台内的真实独立性是**不同作者账号**:有作者按作者计,缺作者退回域名,**缺作者绝不
    当成一个独立来源**(规则 6:缺失 ≠ 有)。
    """
    value = (author or "").strip()
    if value:
        return "author", f"author:{value.lower()}"
    if host:
        return "host", f"host:{host}"
    return "none", ""


def _rank_materials(matches: list[dict], db_path: Path, top_k: int = MATERIAL_TOP_K) -> tuple[list[dict], dict]:
    """把 BM25 候选池按「真实爆款先验 + 证据密度」重排,并给出前置门禁要用的取材统计。

    先验只认本类目库里 ``tier=measured`` 且有分数的真实标签(与 ``keywords.score_keywords``
    同一套词表和权重),未标定类目退化为纯 BM25 并如实记 ``calibration_status="uncalibrated"``
    —— 缺数据不虚构加分也不凭空扣 0 分(规则 6)。证据密度:全文行优先 + 独立来源计数
    (作者优先,缺作者退回域名;见 ``_independence_key``)。
    """
    con = connect(db_path)
    try:
        calibrated = viral.load_calibration(con) is not None
        measured = keywords._measured_label_map(con) if calibrated else {}
        pool: list[dict] = []
        for index, match in enumerate(matches):
            row = con.execute(
                "SELECT id, title, url, author, completeness, coalesce(cleaned_content, content) AS text "
                "FROM articles WHERE id = ?",
                (match["article_id"],),
            ).fetchone()
            if not row:
                continue
            host = _source_host(row["url"])
            axis, key = _independence_key(row["author"], host)
            pool.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "url": row["url"],
                    "text": row["text"] or "",
                    "completeness": row["completeness"],
                    "host": host,
                    "author": (row["author"] or "").strip(),
                    "source_key": key,
                    "source_axis": axis,
                    "bm25": float(match.get("rank") or 0.0),
                    "pool_index": index,
                    "label": measured.get(int(row["id"])),
                }
            )
    finally:
        con.close()

    ranks = [item["bm25"] for item in pool]
    best, worst = (min(ranks), max(ranks)) if ranks else (0.0, 0.0)
    for item in pool:
        # bm25() 越小越相关 ⇒ 归一到 0..1,爆款项最多占 VIRAL_WEIGHT(与关键词评分同一口径)
        norm = 1.0 if worst == best else (worst - item["bm25"]) / (worst - best)
        prior = keywords.LABEL_VALUE.get(item["label"]) if item["label"] else None
        item["viral_prior"] = prior
        item["rank_score"] = (
            round(norm + keywords.VIRAL_WEIGHT * prior, 4) if prior is not None else round(norm, 4)
        )

    ordered = sorted(
        pool,
        key=lambda item: (
            item["completeness"] != cleaner.COMPLETENESS_FULL,
            -item["rank_score"],
            item["pool_index"],
        ),
    )
    selected = ordered[:top_k]
    is_full = cleaner.COMPLETENESS_FULL
    label_counts: dict[str, int] = {}
    for item in pool:
        if item["label"]:
            label_counts[item["label"]] = label_counts.get(item["label"], 0) + 1
    def independent(items: list[dict]) -> int:
        return len({item["source_key"] for item in items if item["source_key"]})

    def hosts(items: list[dict]) -> int:
        return len({item["host"] for item in items if item["host"]})

    axes = {"author": 0, "host": 0, "none": 0}
    for item in pool:
        axes[item["source_axis"]] += 1
    stats = {
        "pool_limit": len(matches),
        "candidates": len(pool),
        "full_candidates": sum(1 for item in pool if item["completeness"] == is_full),
        "distinct_sources": independent(pool),
        "distinct_hosts": hosts(pool),
        "independence_axis": {
            "unit": "author(缺作者退回 host,缺作者不计数)",
            "articles_counted_by_author": axes["author"],
            "articles_counted_by_host": axes["host"],
            "articles_without_any_source_key": axes["none"],
        },
        "selected_ids": [item["id"] for item in selected],
        "selected_full": sum(1 for item in selected if item["completeness"] == is_full),
        "selected_distinct_sources": independent(selected),
        "selected_distinct_hosts": hosts(selected),
        "calibration_status": "calibrated" if calibrated else "uncalibrated",
        "priors": {
            "applied": calibrated,
            "weight": keywords.VIRAL_WEIGHT,
            "measured_label_hits": sum(1 for item in pool if item["label"]),
            "label_counts": label_counts,
            "reason": "" if calibrated else "本类目缺 viral_calibration ⇒ 爆款项不参与排序,退化为纯 BM25",
        },
        "scores": [
            {
                "id": item["id"],
                "rank_score": item["rank_score"],
                "bm25": item["bm25"],
                "viral_prior": item["viral_prior"],
                "completeness": item["completeness"],
                "host": item["host"],
                "author": item["author"],
                "source_axis": item["source_axis"],
            }
            for item in selected
        ],
    }
    materials = [
        {"id": item["id"], "title": item["title"], "url": item["url"], "text": item["text"]}
        for item in selected
    ]
    return materials, stats


def _material_gate(stats: dict, min_full: int, min_sources: int) -> list[str]:
    """取材前置门禁:密度不够就直接拦下,不烧模型。

    根因(2026-11-17 14:36 三次 rejected):1 篇材料扛着「50 多个国家」这类标题数字,
    引句绑不上 ⇒ 数字落在引句窗口外。先查密度比让模型瞎写再被事实门禁拦更省。
    """
    critical = []
    if stats["full_candidates"] < min_full:
        critical.append(
            f"取材密度不足:候选里全文文章 {stats['full_candidates']} 篇 < {min_full}(短稿撑不起引句)"
        )
    if stats["distinct_sources"] < min_sources:
        axis = stats["independence_axis"]["unit"]
        critical.append(
            f"来源独立性不足:候选里独立来源 {stats['distinct_sources']} 个 < {min_sources}"
            f"(口径 {axis};单源改写风险)"
        )
    return critical


def write_article(
    category: str,
    topic: str,
    model_call=None,
    max_revisions: int | None = None,
    max_tokens: int | None = None,
    enable_thinking: bool = False,
    db_path: Path | None = None,
    export_dir: Path | None = None,
    gate_min_score: float | None = None,
    gate_min_chars: int | None = None,
    material_min_full: int | None = None,
    material_min_sources: int | None = None,
    independent_review: bool | None = None,
    review_call=None,
) -> dict:
    writing_cfg = cfg.load_config().get("writing", {})
    if max_revisions is None:
        max_revisions = int(writing_cfg.get("gate_max_revisions", 2))
    if max_tokens is None:
        max_tokens = int(writing_cfg.get("draft_max_tokens", 900))
    if gate_min_score is None:
        gate_min_score = float(writing_cfg.get("gate_min_score", 0.6))
    if gate_min_chars is None:
        gate_min_chars = int(writing_cfg.get("gate_min_chars", 400))
    if material_min_full is None:
        material_min_full = int(writing_cfg.get("material_min_full", 6))
    if material_min_sources is None:
        material_min_sources = int(writing_cfg.get("material_min_sources", 3))
    model_call = model_call or default_model_call
    store.migrate_articles_db(category, db_path=db_path)
    db_path = store.articles_db_path(category) if db_path is None else Path(db_path)

    matches = rag.search(category, topic, limit=MATERIAL_POOL_LIMIT, db_path=db_path)
    materials, material_stats = _rank_materials(matches, db_path)
    LOG.info(
        "writing.materials",
        category=category,
        topic=topic,
        full=f"{material_stats['full_candidates']}/{material_stats['candidates']}",
        sources=f"{material_stats['distinct_sources']}",
        axis=material_stats["independence_axis"]["unit"],
        calibration=material_stats["calibration_status"],
        prior_hits=material_stats["priors"]["measured_label_hits"],
    )
    gate_critical = _material_gate(material_stats, material_min_full, material_min_sources)
    if gate_critical:
        LOG.warn(
            "writing.insufficient_materials",
            category=category,
            topic=topic,
            full=f"{material_stats['full_candidates']}/{material_min_full}",
            sources=f"{material_stats['distinct_sources']}/{material_min_sources}",
        )
        return {
            "category": category,
            "topic": topic,
            "status": "insufficient_materials",
            "passed": False,
            "model_calls": 0,
            "critical": gate_critical,
            "issues": gate_critical,
            "materials": len(materials),
            "material_stats": material_stats,
            "notes": "取材前置门禁拦下,未调用模型:" + ";".join(gate_critical),
            "published": False,
        }

    materials_text = build_materials(materials)
    outline = model_call(
        prompts.outline_prompt(topic, materials_text),
        # 400 每次都被截断(生产日志 2026-11-18 三个版本各触发一次 400→600 重试),
        # 等于每版固定浪费一次模型调用;直接给到实测够用的 600。
        max_tokens=600,
        temperature=0.3,
        enable_thinking=enable_thinking,
    )
    draft = model_call(
        prompts.draft_prompt(topic, outline, materials_text),
        max_tokens=max_tokens,
        temperature=0.4,
        enable_thinking=enable_thinking,
    )
    complete = _is_complete(draft, gate_min_chars)
    retry_cap = int(writing_cfg.get("draft_retry_max_tokens", 2048))
    if not complete and retry_cap > max_tokens:
        LOG.warn("writing.incomplete_retry", topic=topic, max_tokens=max_tokens)
        draft = model_call(
            prompts.draft_prompt(topic, outline, materials_text),
            max_tokens=min(max_tokens * 2, retry_cap),
            temperature=0.4,
            enable_thinking=enable_thinking,
        )
        complete = _is_complete(draft, gate_min_chars)

    claims_text = model_call(
        prompts.claims_prompt(draft, list(range(1, len(materials) + 1))),
        max_tokens=CLAIMS_MAX_TOKENS,
        temperature=0.0,
        enable_thinking=enable_thinking,
    )
    claims = parse_claims(claims_text)
    evidence = verify_claims(claims, materials)

    draft_id = _new_draft_id(category, topic)
    version = 1
    model_meta = _model_meta(model_call)
    # 版本行必须记录实际出稿模型(P13 实测 13/13 行 model_id 为 NULL,无法追责到模型)
    served_model = model_meta.get("served_model")
    _save_version(db_path, draft_id, category, topic, version, draft, served_model)
    review = _verified_review(model_call, draft, materials, evidence, enable_thinking, topic)
    _save_evaluation(db_path, draft_id, version, review, model_meta, evaluator=served_model or "model_review")

    passed = _passed(complete, review, gate_min_score)
    revisions = 0
    while not passed and revisions < max_revisions:
        draft = model_call(
            prompts.revise_prompt(draft, review["issues"] + review["critical"]),
            max_tokens=max_tokens,
            temperature=0.4,
            enable_thinking=enable_thinking,
        )
        complete = _is_complete(draft, gate_min_chars)
        version += 1
        revisions += 1
        _save_version(db_path, draft_id, category, topic, version, draft, served_model)
        # 修订后的草稿必须重新抽取声明并重新核验(旧证据不覆盖新文本)
        claims_text = model_call(
            prompts.claims_prompt(draft, list(range(1, len(materials) + 1))),
            max_tokens=CLAIMS_MAX_TOKENS,
            temperature=0.0,
            enable_thinking=enable_thinking,
        )
        claims = parse_claims(claims_text)
        evidence = verify_claims(claims, materials)
        review = _verified_review(model_call, draft, materials, evidence, enable_thinking, topic)
        _save_evaluation(db_path, draft_id, version, review, model_meta, evaluator=served_model or "model_review")
        passed = _passed(complete, review, gate_min_score)

    # 独立评审只在门禁已通过时跑(账本 T-04):被事实门禁拦下的稿子不值得再花一次评审配额。
    # 通道不可用/模型同名 ⇒ 独立性按不成立处理,绝不为了好看把 same_model_review 改成 false。
    review_outcome: dict | None = None
    review_error: str | None = None
    want_review = review_channel.review_config().get("enabled", False) if independent_review is None else independent_review
    if passed and (want_review or review_call is not None):
        try:
            review_outcome = review_call(draft) if review_call is not None else review_channel.review(draft)
        except review_channel.ReviewUnavailable as exc:
            review_error = str(exc)
    _apply_reviewer(model_meta, review_outcome, review_error)
    if review_outcome:
        _save_evaluation(
            db_path,
            draft_id,
            version,
            {"score": None, "issues": [], "critical": []},
            {
                **model_meta,
                "criteria": "independent_review",
                "prompt": review_outcome["prompt"],
                "verdict": review_outcome["verdict_text"][:6000],
                "usage": review_outcome.get("usage"),
            },
            evaluator=review_outcome["reviewer_model"],
        )
        if review_outcome["verdict"]["blocking"]:
            review = _merge_findings(
                review,
                [],
                ["独立评审判定不通过:" + ",".join(review_outcome["verdict"]["blocking_markers"])],
            )
            passed = False

    # status 决定导出目录与回执口径;writing_history.status 用 T-05 的三态口径(独立复核成立才给独立标签)
    status = "passed" if passed else "rejected"
    account = _review_account(passed, model_meta, review_outcome)
    _set_draft_status(db_path, draft_id, version, account)
    _save_evidence(db_path, draft_id, evidence)

    notes = None
    if not complete:
        notes = "内容完整性检查未通过(疑似截断)"
    elif review["score"] is None:
        notes = "评审输出无法解析,按不合格处理"
    elif review["score"] < gate_min_score:
        notes = f"评审得分 {review['score']} 低于门禁 {gate_min_score}"
    if notes is None and review["critical"]:
        notes = "门禁致命问题: " + ";".join(review["critical"][:3])

    title, _body = _draft_title(draft, topic)
    path = export.write_export(
        category,
        draft_id,
        title,
        topic,
        draft,
        evidence,
        review,
        version,
        export_dir=export_dir,
        status=status,
        notes=notes,
    )
    result = {
        "draft_id": draft_id,
        "title": title,
        "versions": version,
        "revisions": revisions,
        "status": status,
        "passed": passed,
        "truncated": not complete,
        "score": review.get("score"),
        "issues": review.get("issues") or [],
        "critical": review.get("critical") or [],
        "evidence": evidence,
        "path": str(path),
        "materials": len(materials),
        "material_stats": material_stats,
        "notes": notes,
        "model_meta": model_meta,
        "review_account": account,
        "independent_review": _review_summary(review_outcome, review_error),
        "published": False,
    }
    LOG.info(
        "writing.done",
        category=category,
        draft_id=draft_id,
        status=status,
        account=account,
        versions=version,
        reviewer=model_meta.get("reviewer_model"),
    )
    return result
