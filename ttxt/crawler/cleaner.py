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


# ------------------------------------------------------------------ C-1 正文完整性三档

# 含义:**这一行采集到的正文,够不够当“全文语料”用**。判定只看 ``content`` 列本身,
# 不看标题、不看 ``excerpt`` 列(摘要列有字不等于有正文)。
# 阈值 400 与写作门禁同轴(``08_wiki/db_schema.md`` 关键语义:长度 ≥400 才可能 ``passed``),
# 实测语料长度分布在 399→799 之间有明显断层(普查见 ``07_tmp/c1_20261115/census_c1.json``),
# 400 正好落在断层上,不会把“半截正文”误放成全文。
MIN_FULL_CHARS = 400
COMPLETENESS_FULL = "full"
COMPLETENESS_SHORT = "short"
COMPLETENESS_EXCERPT = "excerpt"
COMPLETENESS_VALUES = (COMPLETENESS_FULL, COMPLETENESS_SHORT, COMPLETENESS_EXCERPT)


def usable_length(text: str | None) -> int:
    """计入完整性判定的正文字数(去掉首尾空白后的字符数)。

    与 ``store.USABLE_LEN_SQL``(SQL 侧的同一口径)保持一致;Python 侧的 ``str.strip()``
    覆盖的空白字符是 SQL 版的超集,极边角空白两边可能差 1 字,由 ``store.relabel_completeness``
    在整改时统一改成 SQL 口径,不会长期漂移。
    """
    return len((text or "").strip())


def completeness_of_length(usable_len: int, *, min_full_chars: int = MIN_FULL_CHARS) -> str:
    """唯一的分档规则:0 字 → ``excerpt``;1 ~ min-1 → ``short``;≥ min → ``full``。

    写入侧、体检侧、整改侧都必须走这个函数,不允许各自写一套阈值判断,
    否则同一个词在三个地方含义不同,下游(爆款分析、写作取材)就没法按它筛选。
    """
    if usable_len <= 0:
        return COMPLETENESS_EXCERPT
    if usable_len >= min_full_chars:
        return COMPLETENESS_FULL
    return COMPLETENESS_SHORT


def completeness_of(content: str | None, *, min_full_chars: int = MIN_FULL_CHARS) -> str:
    """按采集到的正文本体判定完整性(见 ``completeness_of_length``)。"""
    return completeness_of_length(usable_length(content), min_full_chars=min_full_chars)

