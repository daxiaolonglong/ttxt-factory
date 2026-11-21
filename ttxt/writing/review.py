"""独立评审通道(账本 T-04):换一个服务给草稿做盲评,用来解除「写作与评审同源」。

三条硬约束:
1. 提示词固定 6 个字「审评这篇文章」+ 正文,不给评分细则 —— 项目负责人 2026-11-17 15:06 定,
   给了细则就会被写作侧的口径诱导,独立评审就失去意义。所以提示词不做成配置项。
2. 密钥只从环境变量或 `.secrets/` 里的文件取,任何日志/回执/异常文本都不得带上密钥值。
3. 通道不可用 ⇒ 独立评审**不成立**,调用方必须按「未解除同源」处理,不得当成通过。

R51 端点策略拆分(项目负责人 2026-11-20 定「DeepSeek 留着审核我们模型写作的文章质量」):
- **评审侧**允许配置里的云端端点,但必须 https、必须配 key;缺 key 或端点不可达显式抛错,
  不静默降级回本地、不悄悄换模型。
- **写作侧**仍强制本地回环,守卫在 ``ttxt.model_gate.client.require_loopback``,本模块不放宽它。
- 评审走云端必须如实记录 ``reviewer_endpoint`` / ``reviewer_model``,``same_model_review``
  照实按两侧真实 id 比较,不许伪装独立评审。
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

from ttxt import config as cfg
from ttxt.logging import get_logger
from ttxt.model_gate import client as model_client

LOG = get_logger("writing.review")

# 项目负责人定死的 6 个字,不改、不配置化
REVIEW_PROMPT = "审评这篇文章"
# 判定只按评审自己写出的结论措辞,不猜语义:命中即算阻断,没命中也不算许可(缺证据就不给独立标签)
BLOCKING_MARKERS = ("不建议发布", "不可发布", "不应发布", "建议拒绝", "不予发布")
# R31(T-25 后续口径对齐):真实评审写的是「暂不建议**直接**发表」「建议大修」,旧的字面标记
# 因为中间插了"直接"而漏判 ⇒ 稿子被记成 AUTO_PASSED。阻断措辞必须按句式识别,不能只比字面。
BLOCKING_PATTERNS = (
    r"不建议(?:直接|立即|马上|现在|公开发表)?(?:公开发表|发表|发布|刊发|上线)",
    r"(?:不宜|不适合|不应|不可|不予|不能)(?:直接|立即|马上|现在)?(?:公开发表|发表|发布|刊发|上线)",
    r"建议(?:大修|重写|退稿|拒稿|拒绝)",
)
PUBLISHABLE_MARKERS = (
    "可以发布",
    "建议发布",
    "可发布",
    "达到发布标准",
    "符合发布标准",
    "可以直接发表",
    "可以发表",
    "建议发表",
)

# R61:长文评审模型(DeepSeek 等)的结论契约 labels_v1。
# 6 字盲评 prompt 一字不动;等模型写完审评后,再让它只从三个标签里选一个,
# 不靠猜语义,也不把「修改后可发布」误当成「可以发布」。
# 规则:可以发布=独立放行;修改后再评/不建议发布=拦截;解析失败=保守拦截。
VERDICT_CONTRACT_LABELS = ("可以发布", "修改后再评", "不建议发布")
VERDICT_CONTRACT_PROMPT = (
    "下面是一篇审评意见。只根据这篇审评意见,在最后一行给出结论,"
    "只能从以下三个标签里选一个:可以发布 / 修改后再评 / 不建议发布。不要写其他内容。"
)


def _contract_label(text: str) -> str | None:
    """从抽取调用的最后几行里找唯一标签;多标签/无标签都返回 None(不猜)。"""
    for raw in reversed((text or "").splitlines()):
        line = re.sub(r"[`*#>\s:：]", "", raw)
        hits = [label for label in VERDICT_CONTRACT_LABELS if label in line]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    return None


def _contract_verdict(label: str | None, error: str | None = None) -> dict:
    """标签→门禁口径。只有「可以发布」放行;其余(含解析失败)一律拦截。"""
    if label == "可以发布":
        return {
            "blocking": False,
            "publishable": True,
            "blocking_markers": [],
            "publishable_markers": [label],
        }
    marker = label if label in ("修改后再评", "不建议发布") else "评审结论无法解析"
    if error:
        marker = f"{marker}({error[:120]})"
    return {
        "blocking": True,
        "publishable": False,
        "blocking_markers": [marker],
        "publishable_markers": [],
    }


class ReviewUnavailable(RuntimeError):
    """通道缺密钥、缺配置或端点不通:独立评审不成立。"""


LOCAL_HOSTS = model_client.LOCAL_HOSTS


def _review_endpoint(section: dict) -> tuple[str, bool]:
    """评审侧端点策略(R51 拆分):回环直接放行;云端必须 https 且是完整地址。

    返回 ``(base_url, is_local)``。任何不合规配置显式抛 ReviewUnavailable ——
    评审「走了哪儿」必须记在明面上,绝不静默换端点、绝不回退到别的通道。
    """
    base_url = str(section.get("base_url") or "").strip()
    if not base_url:
        raise ReviewUnavailable("缺独立评审端点:config.review.base_url 未配置")
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host:
        raise ReviewUnavailable(f"独立评审端点必须是完整的 http(s) 地址:{base_url!r}")
    is_local = host in LOCAL_HOSTS
    if not is_local and parsed.scheme != "https":
        raise ReviewUnavailable(f"云端评审端点必须走 https,密钥不经明文传输:{base_url!r}")
    return base_url, is_local


def review_config() -> dict:
    section = cfg.load_config().get("review")
    return section if isinstance(section, dict) else {}


def api_key(section: dict | None = None, *, required: bool = True) -> str:
    """按 `api_key_env` → `api_key_file` 的顺序取密钥;取不到就抛,不返回空串。

    本地端点通常不校验密钥,调用方传 ``required=False`` 时允许返回空串(即不带 Authorization);
    云端端点(R51)调用方必须传 ``required=True``,取不到 key 就抛,绝不无凭据发请求。
    """
    section = review_config() if section is None else section
    env_name = str(section.get("api_key_env") or "STRATA_API_KEY")
    value = (os.environ.get(env_name) or "").strip()
    if value:
        return value
    file_name = str(section.get("api_key_file") or "").strip()
    if file_name:
        path = cfg.PROJECT_ROOT / file_name
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    if not required:
        return ""
    raise ReviewUnavailable(f"缺独立评审密钥:环境变量 {env_name} 未设,文件 {file_name or '(未配置)'} 不存在或为空")


def served_model(section: dict | None = None) -> str:
    """评审侧真实提供的模型 id。

    探测 ``/v1/models`` 只为**验证配置模型确实在端点可用列表里**;通过后按配置值返回,
    绝不拿列表里"别的那个模型"顶上(云端端点往往一次报好几个 id)。探测不通即抛错。
    """
    section = review_config() if section is None else section
    base_url, is_local = _review_endpoint(section)
    try:
        ids = model_client.models(base_url=base_url, api_key=api_key(section, required=not is_local), timeout=15.0)
    except ReviewUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - 端点不通要如实上报,不猜模型名
        raise ReviewUnavailable(f"独立评审端点探测失败:{type(exc).__name__}") from exc
    configured = str(section.get("model_id") or "")
    if configured:
        if ids and configured not in ids:
            raise ReviewUnavailable(f"独立评审模型不在端点可用列表里:{configured} ∉ {','.join(ids)}")
        return configured
    if ids:
        return ids[0]
    raise ReviewUnavailable("缺独立评审模型:config.review.model_id 未配置,端点也未报告可用模型")


def verdict_markers(text: str) -> dict[str, bool]:
    """按评审原文措辞判定阻断/许可;两类标记都没有 ⇒ 两者都 False(不给独立标签)。"""
    body = text or ""
    blocking = [marker for marker in BLOCKING_MARKERS if marker in body]
    blocking += [m.group(0) for m in (re.search(p, body) for p in BLOCKING_PATTERNS) if m]
    publishable = [marker for marker in PUBLISHABLE_MARKERS if marker in body]
    return {
        "blocking": bool(blocking),
        "publishable": bool(publishable) and not blocking,
        "blocking_markers": blocking,
        "publishable_markers": publishable,
    }


def review(draft: str, section: dict | None = None) -> dict[str, Any]:
    """让评审模型只看「审评这篇文章 + 正文」;返回判定、模型 id、端点与原文,不含密钥。

    走云端时 ``reviewer_endpoint`` 如实记云端地址(绝不写成本地),密钥缺失直接抛错。
    """
    section = review_config() if section is None else section
    base_url, is_local = _review_endpoint(section)
    reviewer_id = served_model(section)
    prompt = f"{REVIEW_PROMPT}\n\n{(draft or '').strip()}"
    try:
        outcome = model_client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=int(section.get("max_tokens", 8000)),
            temperature=float(section.get("temperature", 0.0)),
            base_url=base_url,
            model_id=reviewer_id,
            api_key=api_key(section, required=not is_local),
            timeout_s=float(section.get("request_timeout_s", 300)),
            enable_thinking=bool(section.get("enable_thinking", False)),
        )
    except model_client.ModelError as exc:
        raise ReviewUnavailable(f"独立评审调用失败:{exc}") from exc
    text = outcome.get("text") or ""
    contract = str(section.get("verdict_contract") or "").strip()
    verdict_label: str | None = None
    extraction_text: str | None = None
    extraction_error: str | None = None
    if contract == "labels_v1":
        try:
            extracted = model_client.chat(
                [{"role": "user", "content": VERDICT_CONTRACT_PROMPT + "\n\n" + text[:6000]}],
                max_tokens=int(section.get("verdict_max_tokens", 4096)),
                temperature=0.0,
                base_url=base_url,
                model_id=reviewer_id,
                api_key=api_key(section, required=not is_local),
                timeout_s=float(section.get("request_timeout_s", 300)),
                enable_thinking=False,
            )
            extraction_text = extracted.get("text") or ""
            verdict_label = _contract_label(extraction_text)
        except model_client.ModelError as exc:
            extraction_error = str(exc)[:200]
        verdict = _contract_verdict(verdict_label, extraction_error)
    else:
        verdict = verdict_markers(text)
    LOG.info(
        "writing.review.done",
        reviewer_model=outcome.get("model") or reviewer_id,
        reviewer_endpoint=base_url,
        prompt_chars=len(prompt),
        verdict_chars=len(text),
        verdict_contract=contract or None,
        verdict_label=verdict_label,
        finish_reason=outcome.get("finish_reason"),
        blocking=verdict["blocking"],
        publishable=verdict["publishable"],
        elapsed_s=outcome.get("elapsed_s"),
    )
    return {
        "reviewer_model": outcome.get("model") or reviewer_id,
        "reviewer_endpoint": base_url,
        "reviewer_is_local": is_local,
        "verdict_text": text,
        "verdict": verdict,
        "verdict_contract": contract or None,
        "verdict_label": verdict_label,
        "verdict_extraction": (extraction_text or "")[:1000],
        "verdict_extraction_error": extraction_error,
        "prompt": REVIEW_PROMPT,
        "finish_reason": outcome.get("finish_reason"),
        "usage": outcome.get("usage"),
        "elapsed_s": outcome.get("elapsed_s"),
    }
