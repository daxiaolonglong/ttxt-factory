"""L4 事实核验层(批次4,确定性规则,不调用模型)。

替换 P6/P10 的 bigram overlap≥0.2 启发式证据链:

1. 逐事实绑定:每条 claim 绑定来源文章 id + 原始 URL + **原文连续片段**
   (quote 必为 source_text[start:end] 的连续切片,offset 入库可复核);
2. 分维度核验:数字 / 日期 / 人物 / 机构 / 因果 各自独立提取并在来源全文中
   逐一验证,缺失记入 checks.missing,绝不合并成单一分数;
   **数字与日期锚定在「绑定引句 ±1 句」的连续窗口内**(P13 批次2 根因②修复):
   只要求全文出现,等于允许把正文别处的数字挪到别的断言上冒充真(移花接木);
   窗口内缺失但全文别处出现的锚点单独记入 `checks["numbers_outside_quote"]` /
   `checks["dates_outside_quote"]`,并在 critical 文案里写明,供人工裁定识别。
   人物/机构仍按全文锚定(修复范围克制,不顺手加严),但引句外的命中同样留痕。
3. 事实-观点分离:观点句不进入事实核验,单独记录。
   **路由只认「归属 + 谓语」的明确观点标记**(我认为/笔者/业内人士预计/分析人士判断/
   据 X 报道/据报道/据悉…),**裸 hedge 词不再整句豁免**(P13 批次2 根因①修复):
   「专家小组建议纳入 X」「预计3126年11月15日后有货」这类含日期/数字的机构立场与预测,
   必须继续走事实门禁。显然/或将/可能/或许/应该/必须/建议/预计 等裸 hedge 词只作为
   `checks["opinion_markers"]` 留痕,不构成豁免。
   **命中归属词只是进入观点通道的必要条件(P13 批次4 / 账本 T-15 根因③修复)**:
   第三/四方归属(业内人士预计、据 X 报道、据悉、外媒报道…)必须在**绑定引句 ±1 句**
   的窗口内**逐字**存在 —— 来源里没有这个归属,「业内人士预计,<原句>将继续发酵」就是
   凭空造一个出处给事实句买通行证。找不到即不得路由 opinion,改走事实门禁,
   记入 `checks["attribution"] = {found, missing, self}` 并产生
   `attribution_not_in_source` critical。第一人称自述(我认为/笔者/我的判断…)的出处
   就是作者本人,来源里本不必出现该短语,不做存在性核验。
   反过来,窗口内找到归属也**只是取得转述资格**,不给任何句子盖「已核实」的章。
4. 标题-正文一致性(标题兑现):标题中的数字/日期/人物/机构与词面覆盖必须在
   正文出现;
5. 启发式边界(必须如实记录):人物/机构提取依赖后缀与谓词线索,存在漏检与
   误检;因果仅验证「连接词与绑定引句共现」,不验证因果方向。规则不能证明
   事实为真,只能证明「草稿陈述与来源文本一致」;与来源本身是否为真无关。

credibility 语义(门禁据此判定):
- verified     :引句绑定成功,且数字/日期锚点都在「引句 ±1 句」窗口内找到、人物/机构在来源全文找到;
- unverified   :锚点缺失、引句重叠过低,或第三/四方归属在「引句 ±1 句」窗口内逐字找不到(不通过);
- no_source    :claim 无有效来源编号(不通过);带第三方归属但无来源卡片时同样走这一项;
- opinion      :带明确归属观点标记的句子,且不参与事实门禁,单独记录。
   已修的边界:①裸 hedge 词(建议/预计/或将/可能…)不再把整句豁免出事实门禁 ——
   豁免只按「归属 + 谓语」判定,具名人物/机构的「表示/称/建议」不算观点;
   ②命中归属词但绑定引句 ±1 句里逐字找不到该归属(P13 批次4 / 账本 T-15)不再放行,
   句子回到事实门禁并记 `attribution_not_in_source` critical。
   仍存在的边界:归属表是有限词表,换一种未列写法仍可能被漏判为事实句去做核验;
   漏判的方向是「更严」(多走一次门禁),不是放行。反向边界同样如实记录:
   窗口内逐字存在归属只等于「这句话确是在转述来源写过的话」,规则不判断该归属本身可信与否。
"""

from __future__ import annotations

import re

from ttxt.crawler.simhash import tokenize

QUOTE_MAX_CHARS = 220
QUOTE_MIN_OVERLAP = 0.34  # 引句与 claim 的词面重叠下限(低于此视为未绑定)

# 长度保持的全角→半角归一(1 码点换 1 码点,offset 不错位)
_FW = str.maketrans(
    {
        **{chr(0xFF10 + i): chr(0x30 + i) for i in range(10)},
        "％": "%",
        "．": ".",
        "０": "0",
        "　": " ",
    }
)

NUMBER_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|亿元?|万美元|美元|万元|元|欧元|日元|英镑|个百分点|人次|人|名|家|款|次|岁|辆|架|部|吨|倍)?",
    re.UNICODE,
)
CN_NUMBER_RE = re.compile(r"[一二三四五六七八九十百千万亿两]{1,8}(?:万|亿|千)?(?:余|多|左右)?")
DATE_RE = re.compile(
    r"\d{4}年\d{1,2}月(?:\d{1,2}日)?|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}日|\d{4}年"
)
# 「明年2月」「本月7日」这类**相对日期**DATE_RE 不认,数字会漏进 numbers 变成裸锚点:
# 生产实测(2026-11-18 technology 稿)「计划于明年2月开始运营」因为窗口里找不到孤立的 2
# 被判 critical。整段当日期核验,同时从数字抽取里剔掉,避免同一位数字双计。
REL_DATE_RE = re.compile(
    r"(?:明年|今年|去年|本年|次年|同年|来年|本月|下月|上月|当月)?\d{1,2}月(?:\d{1,2}日)?"
)
PERSON_RE = re.compile(
    r"([\u4e00-\u9fff]{2,4})(?=表示|称|说|指出|强调|透露|介绍|回应|告诉|预计|认为|称)"
)
# 人名与谓语之间常夹副词:原正则会把「华盛顿不断强调」截成「盛顿不断」(生产实测
# 2026-11-18 technology 稿因此多出 persons:盛顿不断 的假 critical)。
# 先剥掉尾部副词,再从原文左侧补回同长度汉字,得到「华盛顿」。
_PERSON_ADVERB_TAIL = re.compile(
    r"(?:不断|持续|正在|已经|仍将|仍然|仍|也已|也|都|还|又|将|正|再度|再次|首次|正式|同时|已|曾)+$"
)
# 4 字窗口会从句子中间起截(「随着华盛顿不断强调」→「着华盛顿」),剥掉开头虚词。
_PERSON_LEADING = set("着了的地得和与在对于从被把向给为是并而及等这那其该随因由据也都还又将正已曾就才")
ORG_RE = re.compile(
    r"[\u4e00-\u9fff]{2,8}?(?:公司|集团|研究院|研究所|科学院|大学|学院|银行|委员会|协会|学会|机构|部门|医院|学校|总局|总部|组织|部委|基金会)"
)
# 指示代词做核心的机构锚点不可核验(「这是该公司」「相关机构」这种),按硬门禁会造假 critical。
# 生产实测:神舟草稿被 「orgs:这是该公司」 这类锚点拦下过。锚点仍留痕,但不决定结论。
ANAPHORIC_ORG_CORES = frozenset(
    """
    这 那 该 此 其 本 这是 那是 这是 这家 那家 这种 那种 上述 前述 本次 此次 这次 各地 多家 相关 旗下
    该公司 该机构 该部门 本公司 此公司 此机构 这些公司 那些公司
    """.split()
)
_ORG_SUFFIXES = (
    "公司", "集团", "研究院", "研究所", "科学院", "大学", "学院", "银行", "委员会", "协会",
    "学会", "机构", "部门", "医院", "学校", "总局", "总部", "组织", "部委", "基金会",
)
# 以这些字开头的「机构核心」是句子片段而不是专名(在/于/与/和/对/从/据…)。
_ORG_FRAGMENT_LEADING = (
    "在", "于", "从", "对", "向", "把", "被", "据", "与", "和", "随", "因", "由",
    "将", "已", "正", "并", "而", "为", "是", "其",
)


def is_anaphoric_org(anchor: str) -> bool:
    """机构锚点去掉后缀后的核心是否只是指示代词/泛指词/句子片段。"""
    for suffix in _ORG_SUFFIXES:
        if anchor.endswith(suffix):
            core = anchor[: len(anchor) - len(suffix)]
            if core in ANAPHORIC_ORG_CORES or core.startswith(("这", "该", "此", "上述", "前述")):
                return True
            # 懒匹配会把句子片段拼成机构:生产实测「在韩国龙仁建设其公司」核心是
            # 「在韩国龙仁建设其」。核心以介词/连词开头或夹代词,就不是可核验的专名。
            if core.startswith(_ORG_FRAGMENT_LEADING) or any(ch in core for ch in "其该此这那"):
                return True
    return False
CAUSAL_RE = re.compile(r"因为|由于|导致|因此|所以|使得|致使|引发|造成|促使|带动|使得")
# 裸 hedge 词:只留痕,不豁免事实门禁(遗留常量,v1 构造脚本 scripts/build_golden_set.py 仍引用)
OPINION_RE = re.compile(
    r"我认为|我们认为|笔者|我个人|个人看来|就个人|显然|据称|据说|传闻|网友|点评|评论员"
    r"|预计|或将|可能|或许|应该|必须|建议|期待|展望|看法|感觉"
)
# 路由到观点的唯一依据:明确「归属 + 谓语」。
# 故意不含「具名人物/机构 + 表示|称|建议|预计」——那是可核验的事实陈述
# (P13 批次1 报告 §7:v2h01 A 级正样本被「建议」整句豁免,完全跳过事实门禁)。
OPINION_ATTRIBUTION_RE = re.compile(
    r"我认为|我们认为|我觉得|笔者|我个人|个人看来|就个人|依我看|在我看来"
    r"|业内人士(?:预计|或将|认为|判断|分析|显然|透露)"
    r"|分析人士(?:认为|预计|预测|判断|看来)"
    r"|业界(?:预计|认为|普遍|猜测)|圈内(?:普遍|预计|认为)"
    r"|据[\u4e00-\u9fffA-Za-z0-9（）()·]{2,14}?(?:报道|消息|爆料|统计|了解|称)"
    r"|据报道|据悉|有消息(?:称|显示|透露)|外媒(?:报道|消息)"
    r"|(?:媒体|博主|网友|评论员)[\u4e00-\u9fff]{0,6}?(?:称|爆料|透露|点评|认为)"
    r"|点评|看法是|我的判断"
)
HEDGE_RE = re.compile(
    r"显然|据称|据说|传闻|预计|或将|可能|或许|应该|必须|建议|期待|展望|看法|感觉"
)
_SENT_END = re.compile(r"[。！？!?；;\n]")


def norm(text: str) -> str:
    return (text or "").translate(_FW)


# 引用编号 [1] / 【2】 是「这句话引用了第几篇材料」的元数据,不是事实数字。
# 生产实测(2026-11-18 technology 稿):5 条事实全部因为句末 [n] 被当成数字锚点
# 判 critical ⇒ 门禁在结构上不可能通过。核验前一律剥掉。
CITATION_RE = re.compile(r"[\[［【]\s*\d{1,2}\s*[\]］】]")


def strip_citations(text: str) -> str:
    return CITATION_RE.sub("", text or "")


def _extract_persons(text: str) -> list[str]:
    out: list[str] = []
    for match in PERSON_RE.finditer(text):
        name = match.group(1)
        trimmed = _PERSON_ADVERB_TAIL.sub("", name)
        if trimmed != name:
            need = len(name) - len(trimmed)
            left = text[max(0, match.start(1) - need): match.start(1)]
            if len(left) == need and all("\u4e00" <= ch <= "\u9fff" for ch in left):
                trimmed = left + trimmed
        trimmed = trimmed.lstrip("".join(_PERSON_LEADING))
        if len(trimmed) >= 2:
            out.append(trimmed)
    return out


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = norm(item).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def _split_date_tokens(normed: str) -> tuple[str, list[str]]:
    """把**相对日期**从数字抽取里剔掉并收进 dates;完整日期原样保留。

    返回 (可用于数字抽取的文本, 日期锚点列表)。
    """
    spans = [(m.start(), m.end()) for m in DATE_RE.finditer(normed)]
    dates = [m.group(0) for m in DATE_RE.finditer(normed)]
    chars = list(normed)
    for match in REL_DATE_RE.finditer(normed):
        if any(start <= match.start() and match.end() <= end for start, end in spans):
            continue  # 已由完整日期覆盖,不重复计
        dates.append(match.group(0))
        for idx in range(match.start(), match.end()):
            chars[idx] = " "
    return "".join(chars), dates


def extract_anchors(text: str) -> dict[str, list[str]]:
    """从文本提取五类事实锚点(去重,保序)。引用编号 [n] 先剥掉,不当事实数字。"""
    clean = strip_citations(text)
    normed = norm(clean)
    # 相对日期(「明年2月」)整段按日期核验,并从数字抽取里剔掉,否则窗口里缺一个孤立的 2
    # 就误判 critical;完整日期(「2026年11月15日」)保持原样 —— T-03 黄金集要求伪造年份
    # 同时以 dates 和 numbers 两个维度报缺失,这条冗余不能拆。
    number_text, dates = _split_date_tokens(normed)
    numbers = _dedup(NUMBER_RE.findall(number_text))
    # 纯中文数量词只在与阿拉伯数字不重叠时补充,避免「三大」与「3」双计
    arabic = "".join(numbers)
    cn = [item for item in _dedup(CN_NUMBER_RE.findall(clean)) if item not in arabic]
    return {
        "numbers": numbers + cn,
        "dates": _dedup(dates),
        "persons": _dedup(_extract_persons(clean)),
        "orgs": _dedup(ORG_RE.findall(clean)),
        "causal": _dedup(CAUSAL_RE.findall(clean)),
    }


def is_opinion(text: str) -> list[str]:
    """路由依据:只返回「明确归属 + 谓语」的观点标记。

    裸 hedge 词(建议/预计/或将/可能…)不再让整句豁免事实门禁 —— 机构立场陈述
    「专家小组建议纳入 X」与日期造假「预计3126年11月15日后有货」都必须继续核验。

    **命中归属词只是进入观点通道的必要条件,不是充分条件**:第三/四方归属还必须
    在来源里逐字存在(见 `attribution_check()`),否则「业内人士预计,…」这种
    无中生有的归属就是把事实句送进免检通道的许可证(P13 批次4 / 账本 T-15)。
    """
    return _dedup(OPINION_ATTRIBUTION_RE.findall(text or ""))


# 自述归属(P13 批次4 / 账本 T-15):出处就是作者本人,来源原文里本来就不必出现这个
# 短语,所以**不做**存在性核验。除这一类以外,`is_opinion()` 命中的归属
# (业内人士预计 / 据 X 报道 / 据报道 / 据悉 / 外媒报道 / 媒体称 / 博主X点评…)
# 都在声称「外部来源说过这话」,必须在绑定引句 ±1 句的窗口内逐字找到。
# 只匹配**整条标记**,避免把「博主张三点评」这类第三方归属误判成作者自述。
SELF_ATTRIBUTION_RE = re.compile(
    r"^(?:我认为|我们认为|我觉得|笔者|我个人|个人看来|就个人|依我看|在我看来"
    r"|点评|看法是|我的判断)$"
)


def attribution_markers(claim: str) -> tuple[list[str], list[str]]:
    """把观点标记拆成(第三方归属, 自述归属)。"""
    marks = is_opinion(claim)
    own = [mark for mark in marks if SELF_ATTRIBUTION_RE.search(mark)]
    return [mark for mark in marks if mark not in own], own


# 「据报道 / 据悉」是泛称,来源里的写法几乎总带主体(「据韩联社报道」「据新华社消息」),
# 逐字匹配会把真实转述判成伪造归属(生产实测 2026-11-18 technology 稿)。
# 只对这两个泛称放宽成「窗口内存在 据…报道」:仍然要求归属与绑定引句同窗,不放行移花接木。
_GENERIC_ATTRIBUTION = {
    "据报道": re.compile(r"据[^。！？；\n]{0,20}?报道"),
    "据悉": re.compile(r"据[^。！？；\n]{0,20}?(?:悉|获悉|了解|消息)"),
}


def _attribution_in(mark: str, window: str) -> bool:
    if mark in (window or ""):
        return True
    pattern = _GENERIC_ATTRIBUTION.get(mark)
    return bool(pattern and pattern.search(window or ""))


def attribution_check(claim: str, window: str) -> dict:
    """T-15:第三/四方归属必须**逐字**出现在「绑定引句 ±1 句」窗口内(确定性,不调模型)。

    与数字/日期同一把尺子:只认窗口,不认全文 —— 正文别处出现过一次「据 X 报道」,
    不等于当前这句的归属有出处(移花接木同样成立)。命中不到就是缺出处,
    该句必须回到事实门禁逐锚点核验,而不是贴「转述」放行。
    无第三方归属(事实句、或纯自述观点)时返回空 dict,不参与核验。
    """
    third_party, own = attribution_markers(claim)
    if not third_party:
        return {}
    found = [mark for mark in third_party if _attribution_in(mark, window)]
    return {
        "found": found,
        "missing": [mark for mark in third_party if mark not in found],
        "self": own,
        "note": "归属须在绑定引句±1句窗口内逐字出现;缺失即视为伪造归属,句子回到事实门禁",
    }


def hedge_markers(text: str) -> list[str]:
    """裸 hedge 词留痕:不参与路由、不豁免门禁,只让人工裁定看见句子带的语气。"""
    return _dedup(HEDGE_RE.findall(text or ""))


def _anchor_in(needle: str, haystack: str) -> bool:
    """数字/日期锚点的包含判断必须带**数字边界**,否则短数字会被长数字吞掉。

    黄金集实测漏洞(v2t13):把「开售仅3天」改成「开售仅10天」后,裸数字 `10`
    被 `10000台` 里的 "10" 命中,伪造数字照样 verified。
    """
    if not needle:
        return False
    if not any(ch.isdigit() for ch in needle):
        return needle in haystack
    # 尾部只在锚点本身以数字结尾时收紧(「2.4亿」后面紧跟「11-17岁」是原文连写,合法);
    # 首部禁止数字/小数点,防止 25 亿元 被 125 亿元、3.5 被 13.5 吞掉。
    tail = r"(?!\d)" if needle[-1].isdigit() else ""
    pattern = rf"(?<![\d.]){re.escape(needle)}{tail}"
    return re.search(pattern, haystack) is not None


def _date_in(anchor: str, haystack: str) -> bool:
    """日期锚点核验:整串命中优先;否则按「年/月/日」分量在同一窗口内分别命中。

    生产实测(2026-11-18):claim「2026年11月7日至8日…」对来源「11月7日至8日，2026首届…」
    被判缺失 —— 字面日期串不连续,但年/月/日三个分量都在同一句里,属于格式差异不是造假。
    分年/月/日核验不会放过伪造日期(2026年11月15日 仍缺 15日)。
    """
    if _anchor_in(anchor, haystack):
        return True
    full = re.fullmatch(r"(\d{4})年(\d{1,2})月(?:(\d{1,2})日)?", anchor)
    if full:
        year, month, day = full.groups()
        if year not in haystack or f"{month}月" not in haystack:
            return False
        return not day or f"{day}日" in haystack
    year_only = re.fullmatch(r"(\d{4})年", anchor)
    return bool(year_only) and year_only.group(1) in haystack


def anchor_window(source_text: str, quote: str, quote_offset: int) -> tuple[str, int]:
    """数字/日期的核验窗口:绑定引句所在句 ±1 句的**连续原文**窗口。

    只查来源全文 = 允许把正文别处出现的数字挪到别的断言上冒充真(移花接木);
    只查引句本身会误伤跨句直引,因此取连续三句。窗口本身仍是原文连续切片,
    offset 可复核。
    """
    text = source_text or ""
    if not quote:
        return text, 0
    idx = text.find(quote, max(quote_offset or 0, 0))
    if idx < 0:
        idx = text.find(quote)
    if idx < 0:
        return quote, 0
    slices = _source_slices(text)
    hit = None
    for i, (chunk, off) in enumerate(slices):
        if off <= idx < off + len(chunk):
            hit = i
            break
    if hit is None:
        return quote, idx
    lo = max(0, hit - 1)
    hi = min(len(slices) - 1, hit + 1)
    start = slices[lo][1]
    end = slices[hi][1] + len(slices[hi][0])
    return text[start:end], start



def _source_slices(text: str) -> list[tuple[str, int]]:
    """把来源切成连续句段 (slice, offset),每段都是原文连续切片。"""
    out: list[tuple[str, int]] = []
    start = 0
    for match in _SENT_END.finditer(text):
        end = match.start() + 1
        chunk = text[start:end]
        if chunk.strip():
            out.append((chunk, start))
        start = end
    if start < len(text) and text[start:].strip():
        out.append((text[start:], start))
    return out


def bind_quote(claim: str, source_text: str) -> tuple[str, int, float] | None:
    """返回 (连续原文引句, 原文偏移, 词面重叠);找不到达标句返回 None。"""
    claim_tokens = set(tokenize(claim))
    if not claim_tokens or not source_text:
        return None
    best: tuple[str, int, float] | None = None
    for chunk, offset in _source_slices(source_text):
        tokens = set(tokenize(chunk))
        if not tokens:
            continue
        overlap = len(claim_tokens & tokens) / len(claim_tokens)
        if best is None or overlap > best[2]:
            best = (chunk, offset, overlap)
    if best is None or best[2] < QUOTE_MIN_OVERLAP:
        return None
    chunk, offset, overlap = best
    stripped = chunk.strip()
    offset += chunk.index(stripped[: min(len(stripped), 1)] or "") if stripped else 0
    if len(stripped) > QUOTE_MAX_CHARS:
        stripped = stripped[:QUOTE_MAX_CHARS]
    return stripped, offset, round(overlap, 3)


def bind_supplied_quote(claim: str, source_text: str, quote: str | None) -> tuple[str, int, float] | None:
    """模型自己给出的引句:必须**逐字连续**存在于来源,且与 claim 词面重叠达标,否则不采信。

    它换的只是"引来源里的哪一句",不降门槛:非逐字、或重叠低于 QUOTE_MIN_OVERLAP,
    一律退回自动绑定。创作链实测(2026-11-18)的拒稿根因是模型把几篇材料的数字
    拼进一句、自动绑定必然绑不上;让模型指明原句能修好"引哪句",但不能放宽"必须逐字"。
    """
    candidate = (quote or "").strip()
    if not candidate or not source_text:
        return None
    claim_tokens = set(tokenize(claim))
    if not claim_tokens:
        return None
    idx = source_text.find(candidate)
    if idx < 0:
        return None
    overlap = len(claim_tokens & set(tokenize(candidate))) / len(claim_tokens)
    if overlap < QUOTE_MIN_OVERLAP:
        return None
    if len(candidate) > QUOTE_MAX_CHARS:
        candidate = candidate[:QUOTE_MAX_CHARS]
    return candidate, idx, round(overlap, 3)


def verify_claim(claim: str, article: dict | None, quote: str | None = None) -> dict:
    """核验单条事实。article 需含 id/title/url/text;quote 为调用方提供的候选引句(可选)。"""
    markers = is_opinion(claim)
    text = (article or {}).get("text") or ""
    supplied = bind_supplied_quote(claim, text, quote) if text else None
    bound = supplied or (bind_quote(claim, text) if text else None)
    window, window_offset = anchor_window(text, bound[0], bound[1]) if bound else ("", 0)
    attribution = attribution_check(claim, window)
    if markers and not attribution.get("missing"):
        # 观点通道:自述归属(我认为/笔者…)或来源里逐字存在归属的转述句。
        # 注意:进入本通道不代表句子为真,只代表它不承担事实核验义务。
        checks: dict = {"opinion_markers": markers}
        if attribution:
            checks["attribution"] = attribution
        return {
            "claim": claim,
            "kind": "opinion",
            "article_id": (article or {}).get("id"),
            "source_url": (article or {}).get("url"),
            "quote": None,
            "quote_offset": None,
            "overlap": 0.0,
            "checks": checks,
            "credibility": "opinion",
        }
    if not article:
        return {
            "claim": claim,
            "kind": "fact",
            "article_id": None,
            "source_url": None,
            "quote": None,
            "quote_offset": None,
            "overlap": 0.0,
            "checks": {"attribution": attribution} if attribution else {},
            "credibility": "no_source",
        }
    anchors = extract_anchors(claim)
    norm_text = norm(text)
    norm_window = norm(window)
    checks: dict = {}
    if supplied:
        checks["quote_source"] = "model_supplied"
    elif bound:
        checks["quote_source"] = "auto_bound"
    if attribution:
        checks["attribution"] = attribution
    # 归属缺出处 ⇒ 这一句已经没有「转述」资格,后续锚点全部照常核验,且必须判不通过。
    all_ok = not attribution.get("missing")
    for dim in ("numbers", "dates", "persons", "orgs"):
        # 根因②修复:数字/日期必须在「绑定引句 ±1 句」窗口内出现;
        # 人物/机构仍按全文锚定(不顺手加严),但引句外的命中会留痕。
        scoped = dim in ("numbers", "dates")
        found, missing, displaced, advisory = [], [], [], []
        for item in anchors[dim]:
            clean = norm(item)
            has_ascii_digit = any(ch.isdigit() and ch.isascii() for ch in clean)
            # 纯中文数量词(一/两/三…)不参与硬门禁:它们在生产实测里大量落在「单一」「一些」
            # 「一次」这类词中,也常与来源的阿拉伯写法(3 名 vs 三名)对不上,按窗口判定会
            # 误杀真实草稿(神舟二十四号草稿被三条这类 critical 拦下)。黄金集 61 张里
            # 这类锚点没有一张是决定性的(带/不带两种口径都是 61/61),因此降为建议项留痕。
            if dim == "numbers" and not has_ascii_digit:
                advisory.append({"anchor": item, "in_source": _anchor_in(clean, norm_text)})
                continue
            if dim == "orgs" and is_anaphoric_org(item):
                advisory.append({"anchor": item, "in_source": _anchor_in(clean, norm_text), "note": "泛指机构锚点不参与硬判定"})
                continue
            windowed = scoped
            matcher = _date_in if dim == "dates" else (lambda a, h: _anchor_in(a, h))
            hit = matcher(clean, norm_window if windowed else norm_text)
            (found if hit else missing).append(item)
            if not hit and windowed and matcher(clean, norm_text):
                displaced.append(item)
        checks[dim] = {"found": found, "missing": missing}
        if displaced:
            checks[f"{dim}_outside_quote"] = displaced
        if advisory:
            checks[f"{dim}_advisory"] = advisory
        all_ok = all_ok and not missing
    hedge = hedge_markers(claim)
    if hedge:
        checks["opinion_markers"] = hedge
        checks["note_opinion"] = "裸 hedge 词留痕,不豁免事实门禁;观点路由只按明确归属判定"
    if bound:
        checks["anchor_window"] = {"offset": window_offset, "chars": len(window)}
    quote_norm = norm(bound[0]) if bound else ""
    if anchors["causal"]:
        # 因果连接词必须与「绑定引句」共现(回退全文会放过无关因果句)
        supported = bool(quote_norm) and all(item in quote_norm for item in anchors["causal"])
        checks["causal"] = {
            "found": [item for item in anchors["causal"] if item in quote_norm],
            "missing": [item for item in anchors["causal"] if item not in quote_norm],
            "note": "仅验证因果连接词与绑定引句共现,不验证因果方向",
        }
        all_ok = all_ok and supported
    else:
        checks["causal"] = {"found": [], "missing": [], "note": "claim 无因果连接词"}
    if bound is None:
        credibility = "unverified"
        all_ok = False
    else:
        credibility = "verified" if all_ok else "unverified"
    return {
        "claim": claim,
        "kind": "fact",
        "article_id": article["id"],
        "source_url": article.get("url"),
        "quote": bound[0] if bound else None,
        "quote_offset": bound[1] if bound else None,
        "overlap": bound[2] if bound else 0.0,
        "anchors": anchors,
        "checks": checks,
        "credibility": credibility,
    }


def quote_verbatim(quote: str | None, offset: int | None, source_text: str) -> bool:
    """复核 quote 确为原文连续切片(验收用,不依赖模型)。"""
    if not quote or offset is None or source_text is None:
        return False
    return source_text[offset : offset + len(quote)] == quote


def check_title(title: str, body: str) -> dict:
    """标题兑现:标题锚点与词面覆盖必须在正文出现。"""
    anchors = extract_anchors(title)
    body_norm = norm(body)
    missing = {}
    for dim in ("numbers", "dates", "persons", "orgs"):
        missing[dim] = [
            item for item in anchors[dim] if not _anchor_in(norm(item), body_norm)
        ]
    missing = {dim: vals for dim, vals in missing.items() if vals}
    title_tokens = set(tokenize(title))
    body_tokens = set(tokenize(body))
    coverage = (
        len(title_tokens & body_tokens) / len(title_tokens) if title_tokens else 1.0
    )
    ok = not any(missing.values()) and coverage >= 0.5
    return {"ok": ok, "missing": {k: v for k, v in missing.items() if v}, "coverage": round(coverage, 3)}


def deterministic_findings(evidence: list[dict], title: str, body: str) -> tuple[list[str], list[str]]:
    """把核验结果转成门禁条目;确定性 critical 不依赖模型评审。"""
    issues: list[str] = []
    critical: list[str] = []
    if not evidence:
        # 生产实测(2026-11-18 technology 稿,3 版全 rejected):声明提取阶段返回空数组,
        # 证据链整段为空,却没有任何确定性 critical —— 门禁只剩模型评审一条腿。
        # 正文带可核验事实锚点而证据为空 ⇒ 要么事实无来源、要么提取失败,两种都必须阻断。
        anchors = extract_anchors(body)
        n_anchors = sum(len(anchors[dim]) for dim in ("numbers", "dates", "persons", "orgs"))
        if n_anchors:
            critical.append(
                f"事实证据链缺失(evidence_empty):正文含 {n_anchors} 个事实锚点,"
                "但声明提取没有产出任何可核验声明——事实无来源或提取失败,不得放行"
            )
    facts = [item for item in evidence if item.get("kind") == "fact"]
    opinions = [item for item in evidence if item.get("kind") == "opinion"]
    for item in facts:
        checks = item.get("checks") or {}
        attribution = checks.get("attribution") or {}
        if attribution.get("missing"):
            # 账本 T-15:句子带着「业内人士预计/据 X 报道」这类归属,但绑定引句 ±1 句里
            # 逐字找不到它 ⇒ 归属是编的。机器可读标识 attribution_not_in_source 固定写在
            # 这一条里,便于门禁、报表与人工复核按标识检索。
            critical.append(
                "伪造归属(attribution_not_in_source):"
                f"{item['claim'][:50]}"
                f"(绑定引句 ±1 句内逐字找不到归属:{','.join(attribution['missing'])})"
            )
        if item["credibility"] == "no_source":
            critical.append(f"无来源断言:{item['claim'][:50]}")
            continue
        if item["credibility"] != "verified":
            missing = {
                dim: payload.get("missing") or []
                for dim, payload in checks.items()
                if dim in ("numbers", "dates", "persons", "orgs", "causal", "attribution")
                and payload.get("missing")
            }
            detail = ";".join(f"{dim}:{','.join(vals)}" for dim, vals in missing.items())
            displaced = {
                dim[: -len("_outside_quote")]: payload
                for dim, payload in (item.get("checks") or {}).items()
                if dim.endswith("_outside_quote") and payload
            }
            if displaced:
                detail += ";" + ";".join(
                    f"{dim}锚点不在引句窗口但在正文别处出现(移花接木嫌疑):{','.join(vals)}"
                    for dim, vals in displaced.items()
                )
            critical.append(
                f"事实未通过来源核验:{item['claim'][:50]}" + (f"({detail})" if detail else "(原文引句未绑定)")
            )
    if facts and len(opinions) > len(facts):
        issues.append("正文以观点为主,可核验事实锚点不足")
    title_check = check_title(title, body)
    if not title_check["ok"]:
        parts = [f"{dim}:{','.join(vals)}" for dim, vals in title_check["missing"].items()]
        parts.append(f"标题词面正文覆盖 {title_check['coverage']:.0%}")
        issues.append("标题-正文一致性(标题兑现):" + ";".join(parts))
    return issues, critical
