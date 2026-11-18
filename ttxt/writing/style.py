"""R40 T-10:模板化程度的确定性度量(只测量,不改写作实现)。

复用口径:
- 公式化连接词/套话命中次数;
- 段首句式重复(同一开头在全文内重复出现);
- 整句重复率;
- 句长方差系数(结构过于均匀也是模板化信号);
- 跨稿共享段首(由 ``scripts/template_probe.py`` 汇总)。

该模块不生成文本、不做门禁判断,只提供可复算的读数。
"""

from __future__ import annotations

import re
import statistics
from collections import Counter

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
