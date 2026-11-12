"""正文清洗:空白归一化 + 常见栏目噪声行剔除(保守策略,不破坏正文)。"""

from __future__ import annotations

import re

from .clean import normalize_text

NOISE_LINE_PATTERNS = [
    re.compile(r"^上一篇[:\uFF1A]"),
    re.compile(r"^下一篇[:\uFF1A]"),
    re.compile(r"^阅读原文$"),
    re.compile(r"^微信扫一扫"),
    re.compile(r"^关注(我们|公众号)"),
    re.compile(r"^版权声明"),
    re.compile(r"^免责声明[:\uFF1A]"),
    re.compile(r"^责任编辑[:\uFF1A]"),
    re.compile(r"^编辑[:\uFF1A]\s*\S{1,10}$"),
]


def clean_article(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""
    lines = [line for line in normalized.splitlines() if not any(p.search(line) for p in NOISE_LINE_PATTERNS)]
    return "\n".join(lines).strip()


def text_length(text: str) -> int:
    return len(text or "")
