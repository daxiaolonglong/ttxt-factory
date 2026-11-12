"""写作工作流:取 material → 大纲 → 正文 → 事实声明核验 → 反向评审 → 修改 → 导出。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from ttxt import config as cfg
from ttxt.analysis import rag
from ttxt.crawler import store
from ttxt.crawler.simhash import tokenize
from ttxt.db import connect
from ttxt.logging import get_logger
from ttxt.model_gate import gate

from . import export, prompts

LOG = get_logger("writing")
MAX_CLAIMS = 5


def default_model_call(
    prompt: str,
    max_tokens: int = 800,
    temperature: float = 0.3,
    enable_thinking: bool = False,
) -> str:
    outcome = gate.submit_and_run(
        "writing",
        {
            "prompt": prompt,
            "max_tokens": max_tokens,
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
    return done[-1]["result"]["text"]


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
    if score is None:
        match = re.search(r"(?:score|评分)\D{0,4}(0(?:\.\d+)?|1(?:\.0+)?)", cleaned)
        if match:
            score = float(match.group(1))
    if score is not None:
        score = max(0.0, min(1.0, score))
    return {"score": score, "issues": issues, "raw": cleaned[:2000]}


def build_materials(materials: list[dict]) -> str:
    if not materials:
        return "（无可用参考材料;请避免陈述具体事实,无法确认处写“待核实”）"
    lines = []
    for index, material in enumerate(materials, start=1):
        snippet = (material["text"] or "").replace("\n", " ")[:240]
        lines.append(f"[{index}] {material['title']} — {snippet}")
    return "\n".join(lines)


def verify_claims(claims: list[dict], materials: list[dict]) -> list[dict]:
    evidence: list[dict] = []
    for item in claims:
        number = int(item.get("article_id") or 0)
        if 1 <= number <= len(materials):
            material = materials[number - 1]
            claim_tokens = set(tokenize(item["claim"]))
            material_tokens = set(tokenize(material["text"]))
            overlap = (len(claim_tokens & material_tokens) / len(claim_tokens)) if claim_tokens else 0.0
            credibility = "supported" if overlap >= 0.2 else "weak"
            evidence.append(
                {
                    "claim": item["claim"],
                    "article_id": material["id"],
                    "overlap": round(overlap, 3),
                    "credibility": credibility,
                }
            )
        else:
            evidence.append(
                {"claim": item["claim"], "article_id": None, "overlap": 0.0, "credibility": "no_source"}
            )
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
            "INSERT OR REPLACE INTO writing_history(draft_id, category, topic, version, content, model_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (draft_id, category, topic, version, content, model_id, cfg.now_iso_ms()),
        )
        con.commit()
    finally:
        con.close()


def _save_evaluation(db_path: Path, draft_id: str, version: int, review: dict) -> None:
    con = connect(db_path)
    try:
        con.execute(
            "INSERT INTO evaluation_history(draft_id, version, evaluator, criteria_json, score, issues_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                draft_id,
                version,
                "model_review",
                json.dumps({"criteria": "facts/structure/title/plain"}, ensure_ascii=False),
                review.get("score"),
                json.dumps(review.get("issues") or [], ensure_ascii=False),
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
            "INSERT INTO evidence_links(draft_id, claim, article_id, quote, overlap, credibility, created_at) VALUES (?,?,?,?,?,?,?)",
            [
                (
                    draft_id,
                    item["claim"],
                    item.get("article_id"),
                    None,
                    item.get("overlap"),
                    item.get("credibility"),
                    cfg.now_iso_ms(),
                )
                for item in evidence
            ],
        )
        con.commit()
    finally:
        con.close()


def _review(model_call, draft: str, evidence: list[dict], enable_thinking: bool) -> dict:
    evidence_text = (
        "\n".join(
            f"- {item['claim']}（来源:{item.get('article_id') or '无'},可信度:{item['credibility']}）"
            for item in evidence
        )
        or "（无证据）"
    )
    text = model_call(
        prompts.review_prompt(draft, evidence_text),
        max_tokens=500,
        temperature=0.0,
        enable_thinking=enable_thinking,
    )
    return parse_review(text)


def write_article(
    category: str,
    topic: str,
    model_call=None,
    max_revisions: int = 2,
    max_tokens: int = 900,
    enable_thinking: bool = False,
    db_path: Path | None = None,
    export_dir: Path | None = None,
) -> dict:
    model_call = model_call or default_model_call
    store.migrate_articles_db(category, db_path=db_path)
    db_path = store.articles_db_path(category) if db_path is None else Path(db_path)

    matches = rag.search(category, topic, limit=5, db_path=db_path)
    con = connect(db_path)
    try:
        materials: list[dict] = []
        for match in matches:
            row = con.execute(
                "SELECT id, title, coalesce(cleaned_content, content) AS text FROM articles WHERE id = ?",
                (match["article_id"],),
            ).fetchone()
            if row:
                materials.append({"id": row["id"], "title": row["title"], "text": row["text"] or ""})
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
    review = _review(model_call, draft, evidence, enable_thinking)
    _save_evaluation(db_path, draft_id, version, review)

    revisions = 0
    while review["issues"] and revisions < max_revisions:
        draft = model_call(
            prompts.revise_prompt(draft, review["issues"]),
            max_tokens=max_tokens,
            temperature=0.4,
            enable_thinking=enable_thinking,
        )
        version += 1
        revisions += 1
        _save_version(db_path, draft_id, category, topic, version, draft, None)
        review = _review(model_call, draft, evidence, enable_thinking)
        _save_evaluation(db_path, draft_id, version, review)

    _save_evidence(db_path, draft_id, evidence)
    title = (draft.strip().splitlines() or [topic])[0][:80]
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
    )
    result = {
        "draft_id": draft_id,
        "title": title,
        "versions": version,
        "revisions": revisions,
        "score": review.get("score"),
        "issues": review.get("issues") or [],
        "evidence": evidence,
        "path": str(path),
        "materials": len(materials),
    }
    LOG.info(
        "writing.done",
        category=category,
        draft_id=draft_id,
        versions=version,
        materials=len(materials),
    )
    return result
