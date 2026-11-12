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
5. 直接输出文章,不要解释。"""


def claims_prompt(draft: str, article_numbers: list[int]) -> str:
    numbers = ",".join(str(n) for n in article_numbers) or "0"
    return f"""阅读下面文章,列出 3-5 个关键事实陈述及其依据的文章编号。
文章:
{draft}
可用文章编号:{numbers}
只输出 JSON 数组,不要其他文字,格式:
[{{"claim":"事实陈述","article_id":1}}]
若某条事实无对应文章,article_id 用 0。"""


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
