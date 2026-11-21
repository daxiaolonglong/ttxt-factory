# -*- coding: utf-8 -*-
"""G5:从**实测探针回执**机械生成写作 few-shot 库,只抽取、不创作。

口径(违反任何一条直接报错退出,不写库):
- 输入必须是 pass_rate_probe 的输出 JSON(带 runs 与每 run 的 passed/score/fact_check_rate/
  primary/primary_detail/draft_id),配合探针导出的草稿 .md(标题与正文节选从这里逐字截取);
- 正例 = passed 且 score ≥ 0.8 且 fact_check_rate ≥ 1.0 的 run;反例 = 未过且
  primary 以 fact. 开头(绑定/锚点类失败)的 run —— 都是机器实测读数,不含人工判断;
- 每类目最多 3 正例 + 3 反例(合计 ≤ fewshot.FEWSHOT_MAX=6);
- 找不到对应导出 .md 的 run 跳过并如实报数,绝不拿别的文本冒充;
- 输出 = 00_control/fewshot_library.json,覆盖写(样本只来自本次给定的证据文件)。

用法(项目 venv):
    .venv\\Scripts\\python.exe scripts/build_fewshot_library.py \
        --probe 03_intelligence/pass_rate_probe-20261121-r61.json \
        --exports 07_tmp/r49_passrate/exports \
        [--out 00_control/fewshot_library.json] [--check]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ttxt.writing.fewshot import FEWSHOT_MAX  # noqa: E402

DEFAULT_OUT = ROOT / "00_control" / "fewshot_library.json"
VIRAL_MIN_SCORE = 0.8
VIRAL_MIN_FC = 1.0
PER_CATEGORY_VIRAL = 3
PER_CATEGORY_JUNK = 3
EXCERPT_MAX_CHARS = 180
SENTENCE_END = "。！？!?"


def _sentences(text: str, max_chars: int) -> str:
    """从正文取前几句做节选,按句末标点切,超长硬截。"""
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in SENTENCE_END:
            parts.append(buf)
            buf = ""
            if sum(len(p) for p in parts) >= max_chars:
                break
    if not parts:
        return text[:max_chars]
    excerpt = "".join(parts)
    return excerpt if len(excerpt) <= max_chars else excerpt[:max_chars]


def _load_draft(export_dir: Path, run: dict) -> dict | None:
    """按 run 的 category/draft_id 找导出的草稿 .md,抽出标题与正文。"""
    category = run["category"]
    draft_id = run["draft_id"]
    for folder in ("", "rejected"):
        path = export_dir / "r1" / category / folder / f"{draft_id}.md"
        if path.exists():
            break
    else:
        return None
    text = path.read_text(encoding="utf-8")
    title = None
    body_start = 0
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            body_start = i + 1
            break
    if not title:
        return None
    # 正文 = 标题行之后的段落;跳过元数据行(> 开头)与空行
    body_lines = [
        ln.strip() for ln in lines[body_start:]
        if ln.strip() and not ln.startswith(">") and ln.strip() != title
    ]
    body = "\n".join(body_lines)
    if not body:
        return None
    return {"title": title, "excerpt": _sentences(re.sub(r"\s+", "", body), EXCERPT_MAX_CHARS)}


def build(probe_path: Path, export_dir: Path) -> tuple[dict, dict]:
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    runs = probe.get("runs")
    if not isinstance(runs, list) or not runs:
        raise SystemExit(f"{probe_path}:没有 runs 数组,不是探针输出")
    stats = {
        "runs": len(runs),
        "viral_seen": 0, "viral_used": 0, "viral_missing_export": 0,
        "junk_seen": 0, "junk_used": 0, "junk_missing_export": 0,
    }
    by_category: dict[str, dict[str, list]] = {}
    for run in runs:
        category = run.get("category") or ""
        passed = run.get("passed") is True
        primary = run.get("primary") or "other"
        if passed and (run.get("score") or 0) >= VIRAL_MIN_SCORE and (run.get("fact_check_rate") or 0) >= VIRAL_MIN_FC:
            stats["viral_seen"] += 1
            used, missing = _pick(run, export_dir, category, by_category, "verified_viral",
                                  PER_CATEGORY_VIRAL, kind_note="passed 高分段")
            stats["viral_used"] += used
            stats["viral_missing_export"] += missing
        elif not passed and primary.startswith("fact."):
            stats["junk_seen"] += 1
            used, missing = _pick(run, export_dir, category, by_category, "verified_junk",
                                  PER_CATEGORY_JUNK, kind_note=f"事实门禁反例:primary={primary}")
            stats["junk_used"] += used
            stats["junk_missing_export"] += missing
    samples = []
    for category in sorted(by_category):
        for kind in ("verified_viral", "verified_junk"):
            samples.extend(by_category[category][kind])
    library = {
        "version": 1,
        "note": (
            "G5 写作 few-shot 样本库。只放实测通过的样本(verified=true,kind=verified_viral/verified_junk);"
            "库为空或缺失时写作链路自动降级为无示例(与 R59 viral 先验同一降级纪律)。"
            "本文件由 scripts/build_fewshot_library.py 从探针回执机械生成,手工不填。"
        ),
        "updated_at": None,
        "source_probe": probe_path.name,
        "samples": samples,
    }
    return library, stats


def _pick(run, export_dir, category, by_category, kind, cap, kind_note) -> tuple[int, int]:
    """把 run 转成样本(逐字取自导出件);类目/kind 配额满或导出件缺失都不写。"""
    slot = by_category.setdefault(category, {"verified_viral": [], "verified_junk": []})
    if len(slot[kind]) >= cap:
        return 0, 0
    draft = _load_draft(export_dir, run)
    if draft is None:
        return 0, 1
    slot[kind].append({
        "category": category,
        "topic": run.get("topic") or "",
        "title": draft["title"],
        "excerpt": draft["excerpt"],
        "kind": kind,
        "score": run.get("score"),
        "fact_check_rate": run.get("fact_check_rate"),
        "primary": run.get("primary"),
        "primary_detail": (run.get("primary_detail") or "")[:120],
        "note": kind_note,
        "verified": True,
    })
    return 1, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="探针回执 → 写作 few-shot 库(只抽取实测样本)")
    parser.add_argument("--probe", required=True, help="pass_rate_probe 输出 JSON")
    parser.add_argument("--exports", required=True, help="探针导出目录(含 r1/<类目>/<draft_id>.md)")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--check", action="store_true", help="只核对不写文件")
    args = parser.parse_args(argv)

    probe_path = Path(args.probe)
    export_dir = Path(args.exports)
    if not probe_path.exists():
        return _fail(f"探针输出不存在:{probe_path}")
    if not export_dir.exists():
        return _fail(f"导出目录不存在:{export_dir}")

    library, stats = build(probe_path, export_dir)
    total = len(library["samples"])
    if total > FEWSHOT_MAX * 5:
        return _fail(f"样本总量 {total} 异常,拒绝写库")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print(f"样本:{total} 条(每类目正例≤{PER_CATEGORY_VIRAL} 反例≤{PER_CATEGORY_JUNK})")
    if total == 0:
        return _fail("一个实测样本都没抽出来,拒绝写库(避免拿空库覆盖现有库)")

    if args.check:
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    import datetime as _dt
    library["updated_at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    out.write_text(json.dumps(library, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"few-shot 库 → {out}({total} 条)")
    return 0


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
