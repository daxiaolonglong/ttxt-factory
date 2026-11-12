"""草稿导出:Markdown 文件(正文 + 证据链 + 评审记录)。

门禁:passed → drafts/<类目>/<draft_id>.md;rejected → drafts/<类目>/rejected/<draft_id>.md。
"""

from __future__ import annotations

from pathlib import Path

from ttxt import config as cfg


def render_markdown(
    category: str,
    draft_id: str,
    title: str,
    topic: str,
    content: str,
    evidence: list[dict],
    review: dict,
    version: int,
    status: str = "passed",
    notes: str | None = None,
) -> str:
    lines: list[str] = [
        f"# {title}",
        "",
        f"> 类目:{category} · 草稿ID:{draft_id} · 版本:v{version} · 主题:{topic} · 生成:{cfg.now_iso()} · 状态:{status}",
    ]
    if notes:
        lines += ["", f"> ⚠️ 拒绝原因:{notes}"]
    lines += [
        "",
        "## 正文",
        "",
        content,
        "",
        "## 事实证据链(P12 逐事实绑定:URL + 原文连续引句 + 分维度核验)",
        "",
        "| 陈述 | 类型 | 来源文章ID | 来源URL | 原文证据(连续切片) | offset | 重叠度 | 核验 | 维度缺失 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    if evidence:
        for item in evidence:
            claim = str(item.get("claim", "")).replace("|", "/")
            quote = str(item.get("quote") or "—").replace("|", "/")[:160]
            url = str(item.get("source_url") or "—").replace("|", "/")
            missing_parts = []
            for dim in ("numbers", "dates", "persons", "orgs", "causal"):
                payload = (item.get("checks") or {}).get(dim) or {}
                if payload.get("missing"):
                    missing_parts.append(f"{dim}:{','.join(payload['missing'])}")
            lines.append(
                f"| {claim} | {item.get('kind') or 'fact'} | {item.get('article_id') or '—'} | {url} "
                f"| {quote} | {item.get('quote_offset') if item.get('quote_offset') is not None else '—'} "
                f"| {item.get('overlap') or 0:.2f} | {item.get('credibility')} | {';'.join(missing_parts) or '—'} |"
            )
    else:
        lines.append("| (无) | — | — | — | — | — | — | no_source | — |")
    lines += ["", "## 评审记录", "", f"- 最终评分:{review.get('score')}"]
    issues = review.get("issues") or []
    critical = review.get("critical") or []
    lines.append(f"- 次要问题:{len(issues)}")
    for issue in issues:
        lines.append(f"  - {issue}")
    lines.append(f"- 致命问题:{len(critical)}")
    for item in critical:
        lines.append(f"  - {item}")
    lines += ["", "---", "本文件仅为待人工确认草稿,未经授权不得发布。"]
    return "\n".join(lines)


def write_export(
    category: str,
    draft_id: str,
    title: str,
    topic: str,
    content: str,
    evidence: list[dict],
    review: dict,
    version: int,
    export_dir: Path | None = None,
    status: str = "passed",
    notes: str | None = None,
) -> Path:
    base = Path(export_dir) if export_dir is not None else cfg.WRITING_DIR / "drafts"
    target = base / category
    if status != "passed":
        target = target / "rejected"
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{draft_id}.md"
    path.write_text(
        render_markdown(category, draft_id, title, topic, content, evidence, review, version, status=status, notes=notes),
        encoding="utf-8",
    )
    return path
