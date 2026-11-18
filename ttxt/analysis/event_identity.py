# -*- coding: utf-8 -*-
"""R38:同一事件的**结构化**判定通路(与标题相似度通路并行,不替换)。

为什么要新开一条通路
--------------------
R37 实测:窗口内跨机构同题候选里,标题相似度 ≥ 0.30 的独立对只有 3 对,而至少 5 对
真同事件(Anthropic IPO、行云科技、华为云)标题写成不同角度,落在 0.20–0.25 被阈值拒。
**下调 0.30 会连带放行「同题不同事」的假对**,所以项目负责人裁定:新增一条结构化
通路,标题相似度只作辅助参考。

结构化判据(三条同时成立)
------------------------
1. **发布时间同日**(任一方缺日期即不判);
2. **关键实体高度重合**:正文具名机构/人物/书名号(核心实体)交集 ≥
   ``MIN_SHARED_CORE_ENTITIES``,且关键实体总交集 ≥ ``MIN_SHARED_ENTITIES``;
   英文品牌/带版本号专名算辅助,全大写行业缩写(CPO/NPO/AIGC)只算弱实体,不单独成立;
3. **数字高度重合**:正文阿拉伯数字(剔除日期里的数字)交集 ≥
   ``MIN_SHARED_NUMBERS`` 且重合率(交集/较小集合)≥ ``MIN_SHARED_NUMBER_RATIO``。

实体不用重合率、只用绝对重合数:R38 实测实体集合随正文长度无限膨胀,两个真同事件
的财经长稿比例只有 0.21–0.43,而共享 2–3 个具名机构/人物已经是强证据;比例门槛
反而会把长稿判死。

优先级(先判的规则一票否决,新通路不得绕过)
------------------------------------------
- 同一域名 → 不构成两家独立媒体;
- 身份缺失 / 身份不明 / 聚合平台 → 不算独立(沿用 ``source_identity.independent``);
- **转载 / 同源 / 官方通稿**:两家规范机构 id 相同(含商务部这类官方通稿归一口径)
  一律拒绝;此外正文 5-gram 词片重合度 ≥ ``COPY_CONTAINMENT`` 视为**同稿转载**
  (不同域名、不同署名也一样),结构化通路必须拒绝 —— 转载稿正文重合度天然高于
  独立报道,这一条是防止新通路把转载洗成独立对的关键闸门。

边界(如实记录,不夸大)
----------------------
规则只看「同一天 + 同实体 + 同数字」,不能证明两篇报道的是同一件事的全貌;
同一天同一行业的两件事若共享足够多的机构名与数字仍可能误配。因此该通路必须先用
已知假对(同题不同事 / 转载冒充独立 / 同域名互配)做对照集,0 误配才允许进入窗口集。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ttxt.crawler.simhash import tokenize

from . import source_identity as si

#: 判"同一事件"的结构化阈值(先由小样本人工可核证据标定,再冻结)
#: 关键实体分三档:核心(机构/人物/书名号)、品牌(英文专名/带版本号)、弱(全大写行业缩写)。
#: 只按**绝对重合数**判,不用重合率:实体集合随正文长度无限膨胀,R38 实测两个真同事件
#: 的财经长稿实体比例只有 0.21–0.43,而共享 2–3 个具名机构/人物已经是强证据。
MIN_SHARED_ENTITIES = 2
MIN_SHARED_CORE_ENTITIES = 2
#: 核心实体之外的英文品牌名只作辅助,不单独构成"同一事件"
MIN_SHARED_STRONG_ENTITIES = 2
MIN_SHARED_NUMBERS = 2
MIN_SHARED_NUMBER_RATIO = 0.5
#: 正文 5-gram 词片重合度 ≥ 此值 ⇒ 按同稿转载处理(不同署名也不独立)
COPY_CONTAINMENT = 0.45
#: 正文相似度分母不足时(过短正文)不做同稿判定,但也不放宽结构化判定
MIN_TOKENS_FOR_COPY = 40
#: 机构名核心只是这些泛称时不算强实体("有限公司""股份公司"是后缀碎片,不是专名)
_GENERIC_ORG_CORES = frozenset({"有限", "股份", "控股", "科技", "网络", "信息", "集团", "公司", "企业"})
#: 机构/人物抽取的明显噪声(R38 实测未清洗时抽到"推动公司""发布公告""司宣布与港股上市公司")
_ORG_NOISE_CHARS = ("的", "了", "与", "对", "宣布", "发布", "推动", "实现", "标志", "有利于", "监管要求")
_PERSON_NOISE_WORDS = ("发布", "宣布", "表示", "记者", "编辑", "报道", "公告", "公司", "集团", "认为",
                       "回应", "官方", "明确", "此前")

_LATIN_ENTITY_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,24}(?:\.[0-9]+)?")
_QUOTED_ENTITY_RE = re.compile(r"「([^」]{2,20})」|《([^》]{2,30})》")
#: 采集器存下来的正文里可能带 HTML/RSS 残留(eastmoney 图片块、sspai「查看全文」链接),
#: 不先剥掉会把 `bkquote`/`style`/URL 片段当成"实体",R38 实测因此配出假对。
_TAG_RE = re.compile(r"<!--.*?-->|<[^>]+>", re.S)
_URL_RE = re.compile(r"https?://\S+")
_LATIN_STOP = frozenset("""
the and for with from this that com www http https html shtml htm cn net org inc ltd co
new says said report reports news according also may will has have been but not you your
ai ipo ceo cfo cto coo us uk eu app ios pc vr ar web api http https
""".split())
#: 数字里剔除日期后仍有意义的最短长度:一位数("3")当事件特征太弱
_MIN_DIGITS = 1


@dataclass(frozen=True)
class Signals:
    """一篇正文的结构化特征。"""

    entities: frozenset[str]
    numbers: frozenset[str]
    tokens: tuple[str, ...] = field(default_factory=tuple)

    @property
    def core_entities(self) -> frozenset[str]:
        """核心实体:具名机构/人物/书名号(新闻里最能指认"这一件事"的词)。"""
        return frozenset(e for e in self.entities
                         if e.startswith(("org:", "person:", "quote:")))

    @property
    def strong_entities(self) -> frozenset[str]:
        """强实体:核心实体 + 英文品牌/带版本号专名;全大写缩写不算。"""
        return frozenset(e for e in self.entities if not e.startswith("acronym:"))

    @property
    def shingles(self) -> frozenset[tuple[str, ...]]:
        if len(self.tokens) < 5:
            return frozenset()
        return frozenset(tuple(self.tokens[i:i + 5]) for i in range(len(self.tokens) - 4))


@dataclass(frozen=True)
class Decision:
    same_event: bool
    reasons: tuple[str, ...]
    shared_entities: tuple[str, ...] = ()
    shared_core: tuple[str, ...] = ()
    shared_strong: tuple[str, ...] = ()
    shared_numbers: tuple[str, ...] = ()
    entity_ratio: float = 0.0
    number_ratio: float = 0.0
    copy_containment: float = 0.0

    def evidence(self) -> dict:
        """落盘用:必须写清"凭什么判同/不同",不接受只给结论。"""
        return {
            "same_event": self.same_event,
            "shared_entities": list(self.shared_entities),
            "shared_core": list(self.shared_core),
            "shared_strong": list(self.shared_strong),
            "shared_numbers": list(self.shared_numbers),
            "entity_ratio": round(self.entity_ratio, 3),
            "number_ratio": round(self.number_ratio, 3),
            "copy_containment": round(self.copy_containment, 3),
            "reasons": list(self.reasons),
        }


def _date_spans(text: str) -> list[tuple[int, int]]:
    from ttxt.writing import verifier

    spans = [(m.start(), m.end()) for m in verifier.DATE_RE.finditer(text)]
    spans += [(m.start(), m.end()) for m in verifier.REL_DATE_RE.finditer(text)]
    return spans


def _clean_body(text: str) -> str:
    """剥掉 HTML 注释/标签与裸 URL;只影响特征提取,不改动库内正文。"""
    body = _TAG_RE.sub(" ", text or "")
    body = _URL_RE.sub(" ", body)
    return re.sub(r"[ \t\r\f\v]+", " ", body)


def _entities(text: str) -> set[str]:
    from ttxt.writing import verifier

    out: set[str] = set()
    anchors = verifier.extract_anchors(text)
    for org in anchors["orgs"]:
        if verifier.is_anaphoric_org(org):
            continue
        core = re.sub(r"(?:股份)?有限公司$|公司$|集团$", "", org)
        if core in _GENERIC_ORG_CORES or len(core) < 2 or core.startswith("公司"):
            continue
        if any(noise in core for noise in _ORG_NOISE_CHARS):
            continue
        # 存去掉公司后缀的核心名:"新丝路控股集团有限公司" 与 "新丝路控股集团" 归为一体
        out.add("org:" + core)
    for person in anchors["persons"]:
        if len(person) < 2 or len(person) > 4:
            continue
        if any(noise in person for noise in _PERSON_NOISE_WORDS):
            continue
        out.add("person:" + person)
    for match in _LATIN_ENTITY_RE.finditer(text):
        raw = match.group(0).strip(".-")
        token = raw.lower()
        if len(token) < 2 or token in _LATIN_STOP or token.isdigit():
            continue
        # 全大写缩写是行业词(CPO/NPO/AIGC),只算弱实体;带数字的版本名(GPT-6.2)算强实体
        if raw.isupper() and not any(ch.isdigit() for ch in raw):
            out.add("acronym:" + token)
        else:
            out.add("latin:" + token)
    for match in _QUOTED_ENTITY_RE.finditer(text):
        entity = (match.group(1) or match.group(2) or "").strip()
        if len(entity) >= 2:
            out.add("quote:" + entity)
    return out


def _numbers(text: str) -> set[str]:
    """正文阿拉伯数字(剔掉日期片段里的数字、年份与单个一位数)。"""
    from ttxt.writing import verifier

    spans = _date_spans(text)
    out: set[str] = set()
    for match in verifier.NUMBER_RE.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in spans):
            continue
        raw = re.sub(r"\s+", "", match.group(0)).replace(",", "")
        digits = re.match(r"\d+(?:\.\d+)?", raw)
        if digits is None:
            continue
        value = digits.group(0)
        if re.fullmatch(r"(?:19|20)\d{2}", value):
            continue
        if "." not in value and len(value) <= _MIN_DIGITS:
            continue
        out.add(value)
    return out


def extract_signals(text: str | None) -> Signals:
    body = _clean_body(text or "")
    return Signals(frozenset(_entities(body)), frozenset(_numbers(body)), tuple(tokenize(body)))


def _ratio(shared: set[str], left: set[str], right: set[str]) -> float:
    smaller = min(len(left), len(right))
    return len(shared) / smaller if smaller else 0.0


def _copy_containment(a: Signals, b: Signals) -> float:
    if len(a.tokens) < MIN_TOKENS_FOR_COPY or len(b.tokens) < MIN_TOKENS_FOR_COPY:
        return 0.0
    sa, sb = a.shingles, b.shingles
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def decide(left_text: str | None, right_text: str | None, *,
           left_date: str | None, right_date: str | None,
           left_signals: Signals | None = None,
           right_signals: Signals | None = None,
           min_shared_entities: int = MIN_SHARED_ENTITIES,
           min_shared_core: int = MIN_SHARED_CORE_ENTITIES,
           min_shared_strong: int = MIN_SHARED_STRONG_ENTITIES,
           min_shared_numbers: int = MIN_SHARED_NUMBERS,
           min_number_ratio: float = MIN_SHARED_NUMBER_RATIO) -> Decision:
    """结构化判定:同日 + 关键实体重合(核心/强) + 数字重合;任一条不成立即否。"""
    if not (left_text or "").strip() or not (right_text or "").strip():
        return Decision(False, ("缺少正文,结构化判定不成立",))
    ld, rd = (left_date or "")[:10], (right_date or "")[:10]
    if not ld or not rd:
        return Decision(False, ("任一方缺少发布时间,无法确认同日",))
    if ld != rd:
        return Decision(False, (f"发布时间不同日({ld} vs {rd})",))

    a = left_signals or extract_signals(left_text)
    b = right_signals or extract_signals(right_text)
    shared_e = a.entities & b.entities
    shared_core = a.core_entities & b.core_entities
    shared_strong = a.strong_entities & b.strong_entities
    shared_n = a.numbers & b.numbers
    e_ratio = _ratio(shared_e, a.entities, b.entities)
    n_ratio = _ratio(shared_n, a.numbers, b.numbers)
    copy = _copy_containment(a, b)
    detail = (f"实体重合 {len(shared_e)}(核心 {len(shared_core)},强 {len(shared_strong)},"
              f"比例 {e_ratio:.2f}),数字重合 {len(shared_n)}({n_ratio:.2f}),"
              f"正文 5-gram 重合 {copy:.3f}")

    def deny(reason: str) -> Decision:
        return Decision(False, (f"{reason};{detail}",),
                        tuple(sorted(shared_e)), tuple(sorted(shared_core)),
                        tuple(sorted(shared_strong)), tuple(sorted(shared_n)),
                        e_ratio, n_ratio, copy)

    if copy >= COPY_CONTAINMENT:
        return deny("正文高度重合,按同稿/转载处理(不作为独立对)")
    if len(shared_e) < min_shared_entities:
        return deny(f"关键实体重合不足(<{min_shared_entities})")
    if len(shared_core) < min_shared_core:
        return deny(f"具名机构/人物/书名号重合不足(<{min_shared_core});"
                    "只有英文品牌或行业缩写不算同一事件")
    if len(shared_strong) < min_shared_strong:
        return deny(f"强实体重合不足(<{min_shared_strong})")
    if len(shared_n) < min_shared_numbers or n_ratio < min_number_ratio:
        return deny(f"数字重合不足(<{min_shared_numbers} 或 <{min_number_ratio:.2f})")
    return Decision(True, (f"同日且实体/数字高度重合:{detail}",),
                    tuple(sorted(shared_e)), tuple(sorted(shared_core)),
                    tuple(sorted(shared_strong)), tuple(sorted(shared_n)),
                    e_ratio, n_ratio, copy)


def judge_pair(left: dict, right: dict, identities: dict[str, si.Identity], *,
               signals: dict[str, Signals] | None = None,
               min_shared_entities: int = MIN_SHARED_ENTITIES,
               min_shared_core: int = MIN_SHARED_CORE_ENTITIES,
               min_shared_strong: int = MIN_SHARED_STRONG_ENTITIES,
               min_shared_numbers: int = MIN_SHARED_NUMBERS,
               min_number_ratio: float = MIN_SHARED_NUMBER_RATIO) -> dict:
    """单对判定,严格按优先级:同域名 → 身份 → 转载/同源/官方通稿 → 结构化。

    对照集验证脚本与全量配对走的是**同一个函数**,避免"验证一套、上线另一套"。
    """
    cache = signals if signals is not None else {}
    ident_a = identities.get(left.get("url") or "")
    ident_b = identities.get(right.get("url") or "")
    da = left.get("domain") or si.domain_of(left.get("url"))
    db = right.get("domain") or si.domain_of(right.get("url"))
    title_score = round(si.event_score(left, right), 3)
    result = {"accepted": False, "title_score_aux": title_score, "evidence": None,
              "stage": "domain", "agencies": [getattr(ident_a, "agency", None),
                                              getattr(ident_b, "agency", None)]}
    if da == db:
        result["reason"] = f"同一域名 {da},不构成两家独立媒体"
        return result
    result["stage"] = "identity"
    if ident_a is None or ident_b is None:
        result["reason"] = "缺少来源身份记录"
        return result
    result["stage"] = "independence"
    if not si.independent(ident_a, ident_b):
        result["reason"] = (f"来源不独立:{ident_a.agency}/{ident_a.kind} 与 "
                            f"{ident_b.agency}/{ident_b.kind}")
        return result
    result["stage"] = "structured"
    url_a, url_b = left["url"], right["url"]
    if url_a not in cache:
        cache[url_a] = extract_signals(left.get("text"))
    if url_b not in cache:
        cache[url_b] = extract_signals(right.get("text"))
    decision = decide(
        left.get("text"), right.get("text"),
        left_date=left.get("date"), right_date=right.get("date"),
        left_signals=cache[url_a], right_signals=cache[url_b],
        min_shared_entities=min_shared_entities,
        min_shared_core=min_shared_core,
        min_shared_strong=min_shared_strong,
        min_shared_numbers=min_shared_numbers,
        min_number_ratio=min_number_ratio,
    )
    result["evidence"] = decision.evidence()
    if decision.same_event:
        result["accepted"] = True
        result["reason"] = ";".join(decision.reasons)
    else:
        result["reason"] = "结构化判定否:" + ";".join(decision.reasons)
    return result


def slim_item(item: dict) -> dict:
    """落盘用条目:去掉正文,只留可核字段(正文进 JSON 会把配对报告撑到数百 MB)。"""
    return {key: item[key] for key in
            ("url", "title", "domain", "source", "article_id", "date") if key in item}


def find_pairs_structured(items: list[dict], identities: dict[str, si.Identity], *,
                          min_shared_entities: int = MIN_SHARED_ENTITIES,
                          min_shared_core: int = MIN_SHARED_CORE_ENTITIES,
                          min_shared_strong: int = MIN_SHARED_STRONG_ENTITIES,
                          min_shared_numbers: int = MIN_SHARED_NUMBERS,
                          min_number_ratio: float = MIN_SHARED_NUMBER_RATIO,
                          reject_sample_limit: int = 200) -> tuple[list[dict], list[dict], dict]:
    """结构化通路下的成对/被拒清单与分阶段计数。

    **先做转载/同源/同域名/身份判定,再做实体+数字判定** —— 前面的规则一票否决,
    结构化判定只在前面的规则都放行后才被咨询。

    items 元素需含 ``url`` / ``title`` / ``text`` / ``date``(可选 ``domain``/``source``)。
    返回值第三个元素是分阶段计数:同域名/身份/独立性的被拒只计数不落盘(动辄数万条),
    正文判定阶段的被拒保留证据样本(转载对全部保留,其余最多 ``reject_sample_limit`` 条),
    保证 ``pairs + 全部计数 = 同日扫描对数``。
    """
    pairs: list[dict] = []
    rejected: list[dict] = []
    stats = {"scan_pairs": 0, "same_day_pairs": 0, "domain_rejected": 0,
             "identity_rejected": 0, "independence_rejected": 0,
             "reprint_rejected": 0, "structured_rejected": 0, "accepted": 0}
    signals: dict[str, Signals] = {}
    by_date: dict[str, list[dict]] = {}
    for item in items:
        day = (item.get("date") or "")[:10]
        if not day or not (item.get("text") or "").strip():
            continue
        by_date.setdefault(day, []).append(item)

    for day, same_day in by_date.items():
        for i, left in enumerate(same_day):
            for right in same_day[i + 1:]:
                stats["same_day_pairs"] += 1
                verdict = judge_pair(
                    left, right, identities, signals=signals,
                    min_shared_entities=min_shared_entities,
                    min_shared_core=min_shared_core,
                    min_shared_strong=min_shared_strong,
                    min_shared_numbers=min_shared_numbers,
                    min_number_ratio=min_number_ratio,
                )
                pair = {"left": slim_item(left), "right": slim_item(right),
                        "date": day, "match_mode": "structured",
                        "title_score_aux": verdict["title_score_aux"]}
                if verdict["accepted"]:
                    stats["accepted"] += 1
                    pairs.append({**pair, "score": verdict["title_score_aux"],
                                  "agencies": verdict["agencies"],
                                  "evidence": verdict["evidence"]})
                    continue
                stage = verdict["stage"]
                if stage == "structured":
                    copy = (verdict["evidence"] or {}).get("copy_containment") or 0.0
                    key = "reprint_rejected" if copy >= COPY_CONTAINMENT else "structured_rejected"
                    if key == "reprint_rejected" or len(rejected) < reject_sample_limit:
                        rejected.append({**pair, "reason": verdict["reason"],
                                         "evidence": verdict["evidence"], "stage": stage})
                else:
                    key = {"domain": "domain_rejected", "identity": "identity_rejected",
                           "independence": "independence_rejected"}[stage]
                stats[key] += 1
    candidate = (stats["accepted"] + stats["reprint_rejected"] + stats["structured_rejected"]
                 + stats["domain_rejected"] + stats["identity_rejected"]
                 + stats["independence_rejected"])
    stats["candidate_pairs"] = candidate
    stats["scan_pairs"] = stats["same_day_pairs"]
    stats["pair_independence_share"] = (round(stats["accepted"] / candidate, 4)
                                        if candidate else None)
    return pairs, rejected, stats
