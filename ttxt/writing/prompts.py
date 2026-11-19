"""写作提示词(中文,面向本地推理模型,要求结构化输出)。"""

from __future__ import annotations


def outline_prompt(topic: str, materials: str) -> str:
    return f"""你是中文头条图文作者。请为主题准备写作大纲。
主题:{topic}
参考材料:
{materials}
要求:输出 4-6 条中文大纲,每行一条,以 - 开头;不要输出解释。"""


def draft_prompt(topic: str, outline: str, materials: str) -> str:
    return f"""你是中文头条图文作者。请根据主题与大纲写一篇 500-800 字的中文文章。
主题:{topic}
大纲:
{outline}
参考材料:
{materials}
要求:
1. 第一行是文章标题(不要加 # 号);
2. 正文分 3-5 段;
3. 引用参考材料时在句末标注 [编号];
4. 不得编造材料中没有的时间、数字、人物;无法确认处写"待核实";
5. 不得编造统计数字,即使标注为"预测/模拟/推演"也不允许;
6. 每条事实只依据**一篇**材料,不要把不同材料的时间/数字拼进同一句,并尽量沿用材料原句措辞;
7. 严格按材料的时间口径写作:材料里写"将举办/预计/计划"的事件不得写成已发生;不要加入你对"当前现实时间"的论断或推测;
8. 直接输出文章,不要解释。"""


def claims_prompt(draft: str, article_numbers: list[int]) -> str:
    numbers = ",".join(str(n) for n in article_numbers) or "0"
    return f"""阅读下面文章,列出**每一句**关键事实陈述(带数字/日期/机构/人物的句子一条都不能漏,最多 12 条)。
文章:
{draft}
可用文章编号:{numbers}
要求:
1. claim 照抄文章里承载事实的那一句,不要改写、不要把几篇材料的数字/日期拼进同一句;
2. quote 从所引文章里**逐字复制**一句原文,必须包含 claim 里的数字/日期/人物;
3. quote 必须能在该文章里逐字找到,一个字都不能改;找不到就换一句能逐字找到的;
4. 找不到任何逐字原文时 article_id 用 0、quote 留空。
只输出 JSON 数组,不要其他文字,格式:
[{{"claim":"文章原句","article_id":1,"quote":"来源原文逐字句"}}]"""


def review_prompt(draft: str, evidence_text: str) -> str:
    return f"""你是严格的中文内容评审。评审下面文章,只输出 JSON 对象,不要其他文字:
{{"score":0.0到1.0,"issues":["次要问题1","次要问题2"],"critical":["致命问题:事实错误/编造/无来源断言/内容截断"]}}
评分标准:事实与材料一致 40%,结构清晰 30%,标题吸引力 20%,无空话套话 10%。
存在事实错误、编造、无来源断言或内容截断时,必须写入 critical 数组;否则 critical 为空数组。
文章:
{draft}
证据链:
{evidence_text}"""


def revise_prompt(draft: str, issues: list[str]) -> str:
    issues_text = "\n".join(f"- {issue}" for issue in issues)
    return f"""请根据评审意见修改文章,保持原主题与事实边界,直接输出修改后的完整文章(第一行标题):
评审意见:
{issues_text}
原文:
{draft}"""


def detemplatize_prompt(draft: str, issues: list[str]) -> str:
    issues_text = "\n".join(f"- {issue}" for issue in issues)
    return f"""下面这篇文章的模板化读数超标,请做**定向重写**。
超标读数(每百句命中数):
{issues_text}
重写规则(违反任何一条即为失败):
1. 只处理上述读数涉及的句子:删除或改写公式化连接词/套话(如"首先/与此同时/综上所述"),改为具体表达或直接删除;拆分或替换"不仅…而且""从…到…"等并列模板句式;
2. 所有事实、数字、日期、人名、机构、引用标注[编号]必须逐字保留,不得改写、不得删除、不得新增;
3. 不得删减段落与句子数量(套话词本身除外),不得输出解释、说明或前后缀;
4. 直接输出重写后的完整文章,第一行仍是标题。
原文:
{draft}"""
