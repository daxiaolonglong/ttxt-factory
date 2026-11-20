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


# ---------------------------------------------------------------- R56 入库拒收线(2026-11-20)

# 回答的问题与上面三档**不同**:三档描述"已入库这一行的完整度等级";这条线决定
# "这一轮新采的正文值不值得进库"。从属关系(规则 63):``full>=400`` 只是质量档描述,
# **不是入库标准**;能否入库只看本线。以后任何人不得拿 400 当入库门槛。
# 阈值 800 的取证依据(实测 2026-11-20,详 `08_wiki/reports/r56-intake-quality.md`):
#   - technology 库 912 行正文长度分布:1–299 字 116 行、300–799 字 88 行
#     (谷底 300–399 仅 8 行)、800–999 字 49 行、≥1000 字 659 行(72.3%);
#   - 2026-11-15 普查(`07_tmp/c1_20261115/census_c1.json`,本文件 C-1 段旧注释引用)
#     显示全文语料在 400–799 之间当时**零行**——今天该带的 80 行全部是无长度闸
#     时代后来混入的(摘要级正文、单薄文),正是要挡的对象;
#   - ≥800 是真实全文簇的下沿:800–999 计数 49,是 700–799(22)的 2.2 倍,再往上
#     直接进入主体簇(659 行)。取 400 会放行 628 字这类单薄文,取 1000 会误杀
#     800–999 簇的真实短讯。
# 单一来源纪律:写入侧(runner 入库闸)与一切复检只许引用本常量,不许第二处写死数字。
MIN_INTAKE_BODY_CHARS = 800


def intake_acceptable(content: str | None) -> bool:
    """这一轮采到的正文是否过入库拒收线(口径与三档一致:strip 后字符数)。"""
    return usable_length(content) >= MIN_INTAKE_BODY_CHARS


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

