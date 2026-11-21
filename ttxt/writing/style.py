"""R40 T-10:模板化程度的确定性度量(只测量,不改写作实现)。

复用口径:
- 公式化连接词/套话命中次数;
- 段首句式重复(同一开头在全文内重复出现);
- 整句重复率;
- 句长方差系数(结构过于均匀也是模板化信号);
- 跨稿共享段首(由 ``scripts/template_probe.py`` 汇总)。

度量口径不做门禁判断,只提供可复算的读数。

R48 追加(T-10 第一小步):``budget_eval`` 把读数对照
``00_control/template_budgets.json`` 的类目预算(预算由
``scripts/template_budget_compute.py`` 从现有语料分布算出,不拍脑袋);
``decheck_draft`` 在写作链路"门禁全部通过后"对超阈值段落经注入的
model_call(生产即本地 model_gate 通道)定向重写一次,重写后仍超标只标记、
不拦截、不改变 passed 口径——它不新增门禁,只留可复算的账。
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Callable

# 明确的套话/公式化连接词:出现即计数,不做同义扩展,避免主观加码。
FORMULAIC_MARKERS = (
    "首先", "其次", "再次", "最后", "值得注意的是", "总的来说", "综上所述",
    "不难发现", "不难看出", "由此可见", "换言之", "众所周知", "毋庸置疑",
    "显而易见", "一方面", "另一方面", "与此同时", "在此基础上", "归根结底",
)

# 并列/递进模板句式(正则),用于统计"不仅…而且""从…到…"这类结构复用。
_PARALLEL_PATTERNS = (
    re.compile(r"不仅[^。！？\n]{2,40}?而且"),
    re.compile(r"不仅[^。！？\n]{2,40}?还"),
    re.compile(r"从[^。！？\n]{1,20}?到[^。！？\n]{1,20}?[，,]"),
    re.compile(r"既[^。！？\n]{2,30}?又"),
    re.compile(r"不仅在于[^。！？\n]{2,40}?更在于"),
)

_SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")
_OPENING_LEN = 6


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def _opening(sentence: str) -> str:
    cleaned = re.sub(r"^[\s“”\"'《》【】()（）\-\d.、]+", "", sentence)
    return cleaned[:_OPENING_LEN]


def template_signals(text: str) -> dict:
    """返回单篇模板化读数。空文本返回全 0,不抛异常。"""
    sentences = _sentences(text)
    n = len(sentences)
    markers = {m: (text or "").count(m) for m in FORMULAIC_MARKERS}
    markers = {k: v for k, v in markers.items() if v}
    openings = Counter(_opening(s) for s in sentences if _opening(s))
    repeated_openings = {k: v for k, v in openings.items() if v >= 2}
    sentence_counts = Counter(sentences)
    repeated_sentences = {k: v for k, v in sentence_counts.items() if v >= 2}
    lengths = [len(s) for s in sentences]
    cv = None
    if len(lengths) >= 2 and statistics.fmean(lengths) > 0:
        cv = round(statistics.pstdev(lengths) / statistics.fmean(lengths), 4)
    parallel = sum(len(p.findall(text or "")) for p in _PARALLEL_PATTERNS)
    dup_sentence_tokens = sum(v for v in repeated_sentences.values())
    return {
        "sentences": n,
        "marker_hits": sum(markers.values()),
        "markers": markers,
        "repeated_opening_kinds": len(repeated_openings),
        "repeated_opening_hits": sum(v for v in repeated_openings.values()),
        "repeated_openings": dict(sorted(repeated_openings.items(), key=lambda kv: -kv[1])[:5]),
        "repeated_sentence_kinds": len(repeated_sentences),
        "duplicate_sentence_share": round(dup_sentence_tokens / n, 4) if n else 0.0,
        "parallel_pattern_hits": parallel,
        "sentence_length_cv": cv,
    }


# ------------------------------------------------------------------ R48 预算与去模板化

DEFAULT_BUDGET_PATH = Path("00_control/template_budgets.json")
DEFAULT_MIN_SENTENCES = 12
_MIN_REWRITE_RATIO = 0.6  # 重写产出短于原文 60% 视为被截断/缩水,拒绝采用


def _percentile(values: list[float], p: float) -> float:
    """线性插值分位数(p ∈ [0,1])。与 scripts/template_budget_compute.py 同一实现。"""
    values = sorted(values)
    if not values:
        raise ValueError("空分布没有分位数")
    k = (len(values) - 1) * p
    f = int(k)
    c = min(f + 1, len(values) - 1)
    return values[f] + (values[c] - values[f]) * (k - f)


def load_budget(category: str, budget_path: Path | None = None) -> dict:
    """读取类目预算。fail-open 口径与 model_gate 的"模型故障不击穿主流程"一致:
    文件缺失/损坏/该类目无预算 → status=not_available(调用方必须如实记账,
    不得把 not_available 当 through 处理)。"""
    path = Path(budget_path) if budget_path is not None else DEFAULT_BUDGET_PATH
    if not path.exists():
        return {"status": "not_available", "reason": f"预算文件不存在:{path}"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - JSON/编码损坏必须兜底,不得击穿主流程
        return {"status": "not_available", "reason": f"预算文件不可解析:{exc}"}
    entry = (data.get("budgets") or {}).get(category)
    if not entry:
        return {"status": "not_available", "reason": f"预算文件不含类目 {category}"}
    return {
        "status": "ok",
        "marker_per_100_sent": float(entry["marker_per_100_sent"]),
        "parallel_per_100_sent": float(entry["parallel_per_100_sent"]),
        "basis_drafts": entry.get("basis_drafts"),
        "min_sentences": int(data.get("min_sentences", DEFAULT_MIN_SENTENCES)),
    }


def budget_eval(sig: dict, budget: dict) -> dict:
    """把 template_signals 读数对照类目预算。只判"超没超预算",不决定放行。"""
    if budget.get("status") != "ok":
        return {"status": "not_available", "reason": budget.get("reason")}
    n = int(sig.get("sentences") or 0)
    min_sent = int(budget.get("min_sentences") or DEFAULT_MIN_SENTENCES)
    if n < min_sent:
        return {
            "status": "insufficient_sentences",
            "reason": f"句数 {n} < 度量下限 {min_sent},密度读数不可靠,不做判定",
            "sentences": n,
        }
    marker100 = 100.0 * int(sig.get("marker_hits") or 0) / n
    parallel100 = 100.0 * int(sig.get("parallel_pattern_hits") or 0) / n
    reasons: list[str] = []
    if marker100 > budget["marker_per_100_sent"]:
        reasons.append(
            f"套话/连接词密度 {marker100:.2f}/百句 超预算 {budget['marker_per_100_sent']}"
        )
    if parallel100 > budget["parallel_per_100_sent"]:
        reasons.append(
            f"并列句式密度 {parallel100:.2f}/百句 超预算 {budget['parallel_per_100_sent']}"
        )
    return {
        "status": "over_budget" if reasons else "within_budget",
        "reasons": reasons,
        "marker_per_100_sent": round(marker100, 2),
        "parallel_per_100_sent": round(parallel100, 2),
        "budget": {
            "marker": budget["marker_per_100_sent"],
            "parallel": budget["parallel_per_100_sent"],
        },
        "basis_drafts": budget.get("basis_drafts"),
        "sentences": n,
    }


def paragraphs(text: str) -> list[str]:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def over_budget_paragraphs(text: str, budget: dict) -> list[dict]:
    """定位超预算段落(段级密度只作重写定位线索,预算判定仍以整篇读数为准)。
    预算不可用或句数过少的段落直接跳过——不给"没尺子就动手"的机会。"""
    if budget.get("status") != "ok":
        return []
    out: list[dict] = []
    for idx, para in enumerate(paragraphs(text)):
        if not para.strip():
            continue
        sig = template_signals(para)
        n = sig["sentences"]
        if n < 4:
            continue
        marker100 = 100.0 * sig["marker_hits"] / n
        parallel100 = 100.0 * sig["parallel_pattern_hits"] / n
        if marker100 > budget["marker_per_100_sent"] or parallel100 > budget["parallel_per_100_sent"]:
            out.append(
                {
                    "index": idx,
                    "marker_per_100_sent": round(marker100, 2),
                    "parallel_per_100_sent": round(parallel100, 2),
                    "markers": sig["markers"],
                }
            )
    return out


def decheck_draft(
    draft: str,
    category: str,
    model_call: Callable[[str], str],
    budget_path: Path | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.3,
    enable_thinking: bool = False,
) -> dict:
    """T-10 第一小步:生成后去模板化检查。

    - 整篇读数在预算内 → through,零额外模型调用;
    - 超预算 → 对超阈值段落经 model_call(生产为本地 model_gate 通道)定向重写
      **一次**;重写产出缩水过甚或调用失败 → rewrite_failed,保留原稿并标记;
    - 重写后仍超预算 → still_over_budget,必须标记,**不拦截、不改写 passed 口径**。
    任何分支都如实回账,绝不静默放过。
    """
    from . import prompts  # 局部导入,避免与 prompts 形成环

    budget = load_budget(category, budget_path=budget_path)
    before_sig = template_signals(draft)
    before = budget_eval(before_sig, budget)
    base = {
        "category": category,
        "budget_status": budget.get("status"),
        "before": before,
        "before_signals": {
            k: before_sig[k]
            for k in ("sentences", "marker_hits", "parallel_pattern_hits", "sentence_length_cv")
        },
        "rewrite_count": 0,
        "over_budget_paragraphs": 0,
    }
    if before["status"] != "over_budget":
        return {**base, "draft": draft, "check_status": before["status"]}

    flagged = over_budget_paragraphs(draft, budget)
    issues: list[str] = list(before["reasons"])
    for f in flagged:
        marker_text = "、".join(f"{k}×{v}" for k, v in (f["markers"] or {}).items()) or "无"
        issues.append(
            f"第 {f['index'] + 1} 段:套话 {f['marker_per_100_sent']}/百句"
            f"(命中:{marker_text}),并列句式 {f['parallel_per_100_sent']}/百句"
        )
    try:
        rewritten = model_call(
            prompts.detemplatize_prompt(draft, issues),
            max_tokens=max_tokens,
            temperature=temperature,
            enable_thinking=enable_thinking,
        )
    except Exception as exc:  # noqa: BLE001 - 模型故障不得击穿主流程,但必须留痕
        return {
            **base,
            "draft": draft,
            "check_status": "rewrite_failed",
            "over_budget_paragraphs": len(flagged),
            "error": str(exc)[:300],
        }
    if not rewritten or len(rewritten) < len(draft) * _MIN_REWRITE_RATIO:
        return {
            **base,
            "draft": draft,
            "check_status": "rewrite_failed",
            "over_budget_paragraphs": len(flagged),
            "error": "重写产出为空或长度缩水低于原文 60%,疑似截断,保留原稿",
        }
    after_sig = template_signals(rewritten)
    after = budget_eval(after_sig, budget)
    check_status = "through" if after["status"] == "within_budget" else "still_over_budget"
    return {
        **base,
        "draft": rewritten,
        "check_status": check_status,
        "rewrite_count": 1,
        "over_budget_paragraphs": len(flagged),
        "after": after,
        "after_signals": {
            k: after_sig[k]
            for k in ("sentences", "marker_hits", "parallel_pattern_hits", "sentence_length_cv")
        },
    }


# R61 0 产出根因:本地模型会写出相邻重复片段(如"每4个月延1个月"连写两遍)和重复句。
# 这里做**确定性去重**:只删相邻重复,不改写、不增删事实、不动标题以外内容;修完仍要
# 重抽 claim / 重核事实,去重不替代任何门禁。
_ADJACENT_REPEAT_MIN = 6
_MAX_SENTENCE_FOR_REPEAT_SCAN = 600


def _collapse_adjacent_repeats(sentence: str) -> str:
    """删除句内相邻重复片段(保留一份);找不到重复就原样返回。"""
    s = sentence
    if len(s) > _MAX_SENTENCE_FOR_REPEAT_SCAN:
        return s
    changed = True
    while changed:
        changed = False
        n = len(s)
        for length in range(n // 2, _ADJACENT_REPEAT_MIN - 1, -1):
            for start in range(0, n - 2 * length + 1):
                if s[start:start + length] == s[start + length:start + 2 * length]:
                    s = s[:start + length] + s[start + 2 * length:]
                    changed = True
                    break
            if changed:
                break
    return s


def copyedit_draft(draft: str) -> tuple[str, list[dict]]:
    """确定性校对:删相邻重复句/相邻重复片段,保留第一处;返回(新文本, 修改清单)。"""
    fixes: list[dict] = []
    out_lines: list[str] = []
    for line in (draft or "").splitlines():
        if not line.strip():
            out_lines.append(line)
            continue
        # Markdown 表格的分隔行(|---|---|)本身是合法的重复列语法,不得当重复片段删。
        if line.strip().startswith("|") and re.fullmatch(r"[|\-:\s]+", line.strip()):
            out_lines.append(line)
            continue
        parts = re.findall(r"[^。！？!?；;]+[。！？!?；;]?", line)
        kept: list[str] = []
        for part in parts:
            collapsed = _collapse_adjacent_repeats(part)
            if collapsed != part:
                fixes.append({"kind": "adjacent_repeat", "before": part[:160], "after": collapsed[:160]})
                part = collapsed
            normalized = re.sub(r"\s+", "", part)
            if kept and normalized and re.sub(r"\s+", "", kept[-1]) == normalized:
                fixes.append({"kind": "duplicate_sentence", "before": part[:160]})
                continue
            kept.append(part)
        out_lines.append("".join(kept))
    return "\n".join(out_lines), fixes
