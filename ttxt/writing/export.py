"""草稿导出:Markdown 文件(正文 + 证据链 + 评审记录),仅待人工确认。"""

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
) -> str:
    lines: list[str] = [
        f"# {title}",
        "",
        f"> 类目:{category} · 草稿ID:{draft_id} · 版本:v{version} · 主题:{topic} · 生成:{cfg.now_iso()}",
        "",
        "## 正文",
        "",
        content,
        "",
        "## 事实证据链",
        "",
        "| 陈述 | 来源文章ID | 重叠度 | 可信度 |",
        "|---|---|---|---|",
    ]
    if evidence:
        for item in evidence:
            claim = str(item.get("claim", "")).replace("|", "/")
            lines.append(
                f"| {claim} | {item.get('article_id') or '—'} | {item.get('overlap') or 0:.2f} | {item.get('credibility')} |"
            )
    else:
        lines.append("| (无) | — | — | no_source |")
    lines += ["", "## 评审记录", "", f"- 最终评分:{review.get('score')}"]
    issues = review.get("issues") or []
    lines.append(f"- 剩余问题:{len(issues)}")
    for issue in issues:
        lines.append(f"  - {issue}")
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
) -> Path:
    base = Path(export_dir) if export_dir is not None else cfg.WRITING_DIR / "drafts"
    target = base / category
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{draft_id}.md"
    path.write_text(
        render_markdown(category, draft_id, title, topic, content, evidence, review, version),
        encoding="utf-8",
    )
    return path
