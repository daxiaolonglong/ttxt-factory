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

# R63:R61 现场同一并列项被连写两遍("小步调整、小步调整")、同句跨段重复
# 都漏网(旧实现只比"相邻片段严格相等"和"与本行上一句严格相等")。
# 下面三把尺子都是**确定性**的:只做严格包含/严格相等,不做近似相似删除。
_CLAUSE_SPLIT = re.compile(r"[^，,、；;]+[，,、；;]?")
_CLAUSE_MIN_CHARS = 4  # 短分句可能是有意的反复修辞(如"哈哈哈"),不动刀
_CROSS_LINE_MIN_CHARS = 10  # 归一化短于此不跨行去重:短句误删风险高于收益
_SENT_TAIL = "。！？!?；;，,、"
_CITE_MARKER = re.compile(r"\[\d{1,3}\]$")
_TABLE_SEP = re.compile(r"[|\-:\s]+")
_HEADING = re.compile(r"^#{1,6}\s")


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


def _core(chunk: str) -> str:
    """分句的比较核:去空白、剥分句末尾的连接标点与句末标点。"""
    return re.sub(r"\s+", "", (chunk or "").strip()).rstrip(_SENT_TAIL + "，,、；;")


def _collapse_repeat_clauses(part: str) -> tuple[str, bool]:
    """删除**相邻**的重复并列项(逗号/顿号/分号分隔),保留第一处。

    判定口径(确定性、可复算):当前并列项整体等于前一保留项的**尾部**
    (含严格相等),且长度 ≥ ``_CLAUSE_MIN_CHARS`` 才算重复——
    "坚持小步调整、小步调整、弹性实施" 里第二项会被删,
    "既包括一线城市、一线城市人口" 这种不是尾部重复的不动。
    """
    chunks = [m.group(0) for m in _CLAUSE_SPLIT.finditer(part)]
    if len(chunks) < 2:
        return part, False
    kept: list[str] = []
    prev_core = ""
    changed = False
    for chunk in chunks:
        core = _core(chunk)
        if core and len(core) >= _CLAUSE_MIN_CHARS and prev_core.endswith(core):
            changed = True
            continue
        kept.append(chunk)
        prev_core = core
    if not changed:
        return part, False
    out = "".join(kept).rstrip("，,、；;、 ")
    tail = re.search(r"[。！？!?]$", part)
    if tail and not re.search(r"[。！？!?]$", out):
        out += tail.group(0)
    return out, True


def _normalize_sentence(part: str) -> str:
    """句级去重的比较核:去空白、剥句末引用标记与结尾标点。"""
    text = re.sub(r"\s+", "", part or "").rstrip(_SENT_TAIL)
    text = _CITE_MARKER.sub("", text)
    return text.strip(_SENT_TAIL)


def _cite_marker(part: str) -> str:
    text = re.sub(r"\s+", "", part or "").rstrip(_SENT_TAIL)
    match = _CITE_MARKER.search(text)
    return match.group(0) if match else ""


def _insert_cite(sentence: str, marker: str) -> str:
    """把引用标记插到句末标点之前;同一标记不重复插。"""
    if not marker or marker in sentence:
        return sentence
    match = re.search(r"[。！？!?；;，,、]$", sentence)
    if match:
        return sentence[: match.start()] + marker + sentence[match.start():]
    return sentence + marker


def copyedit_draft(draft: str) -> tuple[str, list[dict]]:
    """确定性校对:删相邻重复片段/重复并列项/重复句,一律保留第一处。

    R63 升级(对应 R61 评审点名的"编校错误/重复表述"):
    - 句级去重从"只比本行上一句"扩到"比本行已保留的任一句";
    - 新增**跨行(跨段)重复句**消除:全篇只保留首现;
    - 新增分句级重复消除(``clause_repeat``);
    - 被删的重复句若带 ``[编号]`` 引用标记而保留句没有,标记**回填**进保留句
      —— 引用证据不能因为去重而丢。
    只做严格相等/尾部包含判定,不做近似相似删除;空行、Markdown 表格分隔行、
    标题行(``#``)一律原样保留。
    """
    fixes: list[dict] = []
    lines_raw = (draft or "").splitlines()
    structures: list[list[str] | None] = []  # None = 该行原样保留
    doc_seen: dict[str, tuple[int, int]] = {}  # 归一化句 → (首现行号, 行内序号)
    for line in lines_raw:
        index = len(structures)
        stripped = line.strip()
        if not stripped:
            structures.append(None)
            continue
        # 表格分隔行是合法的重复列语法;# 标题行不做删句处理。
        if (stripped.startswith("|") and re.fullmatch(_TABLE_SEP, stripped)) or _HEADING.match(stripped):
            structures.append(None)
            continue
        kept: list[str] = []
        for part in re.findall(r"[^。！？!?；;]+[。！？!?；;]?", line):
            collapsed = _collapse_adjacent_repeats(part)
            if collapsed != part:
                fixes.append({"kind": "adjacent_repeat", "before": part[:160], "after": collapsed[:160]})
                part = collapsed
            deduped, clauses_changed = _collapse_repeat_clauses(part)
            if clauses_changed:
                fixes.append({"kind": "clause_repeat", "before": part[:160], "after": deduped[:160]})
                part = deduped
            normalized = _normalize_sentence(part)
            dup_at = next(
                (k for k, prev in enumerate(kept) if normalized and _normalize_sentence(prev) == normalized),
                None,
            )
            if dup_at is not None:
                marker = _cite_marker(part)
                if marker and not _cite_marker(kept[dup_at]):
                    kept[dup_at] = _insert_cite(kept[dup_at], marker)
                fixes.append({"kind": "duplicate_sentence", "before": part[:160], "kept_at": f"行{index + 1}第{dup_at + 1}句"})
                continue
            kept.append(part)
            if normalized:
                doc_seen.setdefault(normalized, (index, len(kept) - 1))
        structures.append(kept)
    for index, kept in enumerate(structures):
        if kept is None or not kept:
            continue
        survivors: list[str] = []
        for part in kept:
            normalized = _normalize_sentence(part)
            earlier = doc_seen.get(normalized)
            # 只删"更晚的行里、与更早某行严格同句"的重复;同行重复在上一轮已处理。
            if earlier and earlier[0] < index and len(normalized) >= _CROSS_LINE_MIN_CHARS:
                first_line, first_pos = earlier
                marker = _cite_marker(part)
                if marker and not _cite_marker(structures[first_line][first_pos]):
                    structures[first_line][first_pos] = _insert_cite(structures[first_line][first_pos], marker)
                fixes.append(
                    {"kind": "cross_line_duplicate", "before": part[:160], "kept_at": f"行{first_line + 1}"}
                )
                continue
            survivors.append(part)
        structures[index] = survivors
    out_lines = [
        raw if kept is None else "".join(kept)
        for raw, kept in zip(lines_raw, structures)
    ]
    return "\n".join(out_lines), fixes


# ------------------------------------------------------------------ R63 未兑现的列举承诺

# 数量承诺:"两件核心大事/三个要点/几步准备"——只认 ≥2 的量词与可数名词,
# "做好一件事"这种单数说法不算承诺(误报宁可漏,不要误伤正文)。
_ENUM_PROMISE = re.compile(
    r"(?P<num>[两二三四五六七八九十]|几|多|数|\d{1,2})\s*"
    r"(?:件|个|项|步|条|大)\s*(?:核心|关键|重要|主要)?\s*"
    r"(?:大事|要点|重点|方面|结论|变化|信号|信息|动作|准备|安排)"
)
# 列举结构:行首编号/项目符号、"一是/二是"、"第一/第二"、带圈数字。
_ENUM_STRUCTURE = re.compile(
    r"(?:^|\n)\s*(?:[-*·•]|\d+\s*[.、)）]|[①②③④⑤⑥⑦⑧⑨])"
    r"|第[两二三四五六七八九十\d]+\s*(?:[件个项条点])?\s*[是为、，,：:]"
    r"|[一二三四五六七八九十]\s*[是为：]"
)


def enum_promise_issues(draft: str) -> list[str]:
    """正文承诺了"N 件大事/几个要点"却没有任何列举结构 ⇒ 给一条修订线索。

    只出**线索**,不出 critical、不参与放行(判定口径见 ``workflow._passed``):
    两位独立评审都点过"标题说两件核心大事、正文从未列举",这类问题可确定性
    检出,但误报代价必须低,所以只在"有数量承诺 + 全篇无列举结构"时报告。
    """
    text = draft or ""
    promise: tuple[str, int] | None = None
    for match in _ENUM_PROMISE.finditer(text):
        if match.group("num") in ("1", "0"):
            continue
        promise = (re.sub(r"\s+", "", match.group(0)), match.start())
        break
    if promise is None or _ENUM_STRUCTURE.search(text):
        return []
    snippet = re.sub(r"\s+", "", text[max(0, promise[1] - 20): promise[1] + 40])
    return [
        f"正文承诺了「{promise[0]}」但没有逐条列举(上下文:{snippet}…):"
        "请把承诺的内容按「一是/二是…」或编号列表逐条写出,或改成不含数量承诺的说法"
    ]
