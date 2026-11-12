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

from ttxt import config as cfg
from ttxt.analysis import rag
from ttxt.crawler import store
from ttxt.db import connect
from ttxt.logging import get_logger
from ttxt.model_gate import client as model_client
from ttxt.model_gate import gate

from . import export, prompts, verifier

LOG = get_logger("writing")
MAX_CLAIMS = 5
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
        claims.append({"claim": claim, "article_id": article_id})
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
        evidence.append(verifier.verify_claim(item["claim"], article))
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


def _set_draft_status(db_path: Path, draft_id: str, status: str) -> None:
    con = connect(db_path)
    try:
        con.execute("UPDATE writing_history SET status=? WHERE draft_id=?", (status, draft_id))
        con.commit()
    finally:
        con.close()


def _save_evaluation(db_path: Path, draft_id: str, version: int, review: dict, meta: dict | None = None) -> None:
    con = connect(db_path)
    try:
        con.execute(
            "INSERT INTO evaluation_history(draft_id, version, evaluator, criteria_json, score, issues_json, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                draft_id,
                version,
                "model_review",
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
    for dim in ("numbers", "dates", "persons", "orgs", "causal"):
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
    """记录「写作与评审同模型」的评审独立性限制与当前 served_model。"""
    meta = {
        "same_model_review": True,
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


def _verified_review(model_call, draft, materials, evidence, enable_thinking, topic):
    """模型评审 + 确定性核验结论合并(标题在此重新绑定)。"""
    review = _review(model_call, draft, evidence, enable_thinking)
    title, body = _draft_title(draft, topic)
    det_issues, det_critical = verifier.deterministic_findings(evidence, title, body)
    return _merge_findings(review, det_issues, det_critical)


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
    model_call = model_call or default_model_call
    store.migrate_articles_db(category, db_path=db_path)
    db_path = store.articles_db_path(category) if db_path is None else Path(db_path)

    matches = rag.search(category, topic, limit=5, db_path=db_path)
    con = connect(db_path)
    try:
        materials: list[dict] = []
        for match in matches:
            row = con.execute(
                "SELECT id, title, url, coalesce(cleaned_content, content) AS text FROM articles WHERE id = ?",
                (match["article_id"],),
            ).fetchone()
            if row:
                materials.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "url": row["url"],
                        "text": row["text"] or "",
                    }
                )
    finally:
        con.close()

    materials_text = build_materials(materials)
    outline = model_call(
        prompts.outline_prompt(topic, materials_text),
        max_tokens=400,
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
        max_tokens=400,
        temperature=0.0,
        enable_thinking=enable_thinking,
    )
    claims = parse_claims(claims_text)
    evidence = verify_claims(claims, materials)

    draft_id = _new_draft_id(category, topic)
    version = 1
    _save_version(db_path, draft_id, category, topic, version, draft, None)
    model_meta = _model_meta(model_call)
    review = _verified_review(model_call, draft, materials, evidence, enable_thinking, topic)
    _save_evaluation(db_path, draft_id, version, review, model_meta)

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
        _save_version(db_path, draft_id, category, topic, version, draft, None)
        # 修订后的草稿必须重新抽取声明并重新核验(旧证据不覆盖新文本)
        claims_text = model_call(
            prompts.claims_prompt(draft, list(range(1, len(materials) + 1))),
            max_tokens=400,
            temperature=0.0,
            enable_thinking=enable_thinking,
        )
        claims = parse_claims(claims_text)
        evidence = verify_claims(claims, materials)
        review = _verified_review(model_call, draft, materials, evidence, enable_thinking, topic)
        _save_evaluation(db_path, draft_id, version, review, model_meta)
        passed = _passed(complete, review, gate_min_score)

    status = "passed" if passed else "rejected"
    _set_draft_status(db_path, draft_id, status)
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
        "notes": notes,
        "model_meta": model_meta,
        "published": False,
    }
    LOG.info("writing.done", category=category, draft_id=draft_id, status=status, versions=version)
    return result
