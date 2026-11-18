"""独立评审通道(账本 T-04):换一个服务给草稿做盲评,用来解除「写作与评审同源」。

三条硬约束:
1. 提示词固定 6 个字「审评这篇文章」+ 正文,不给评分细则 —— 项目负责人 2026-11-17 15:06 定,
   给了细则就会被写作侧的口径诱导,独立评审就失去意义。所以提示词不做成配置项。
2. 密钥只从环境变量或 `.secrets/` 里的文件取,任何日志/回执/异常文本都不得带上密钥值。
3. 通道不可用 ⇒ 独立评审**不成立**,调用方必须按「未解除同源」处理,不得当成通过。
"""

from __future__ import annotations

import os
from typing import Any

from ttxt import config as cfg
from ttxt.logging import get_logger
from ttxt.model_gate import client as model_client

LOG = get_logger("writing.review")

# 项目负责人定死的 6 个字,不改、不配置化
REVIEW_PROMPT = "审评这篇文章"
# 判定只按评审自己写出的结论措辞,不猜语义:命中即算阻断,没命中也不算许可(缺证据就不给独立标签)
BLOCKING_MARKERS = ("不建议发布", "不可发布", "不应发布", "建议拒绝", "不予发布")
PUBLISHABLE_MARKERS = ("可以发布", "建议发布", "可发布", "达到发布标准", "符合发布标准")


class ReviewUnavailable(RuntimeError):
    """通道缺密钥、缺配置或端点不通:独立评审不成立。"""


def review_config() -> dict:
    section = cfg.load_config().get("review")
    return section if isinstance(section, dict) else {}


def api_key(section: dict | None = None) -> str:
    """按 `api_key_env` → `api_key_file` 的顺序取密钥;取不到就抛,不返回空串。"""
    section = review_config() if section is None else section
    env_name = str(section.get("api_key_env") or "DEEPSEEK_API_KEY")
    value = (os.environ.get(env_name) or "").strip()
    if value:
        return value
    file_name = str(section.get("api_key_file") or "").strip()
    if file_name:
        path = cfg.PROJECT_ROOT / file_name
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    raise ReviewUnavailable(f"缺独立评审密钥:环境变量 {env_name} 未设,文件 {file_name or '(未配置)'} 不存在")


def served_model(section: dict | None = None) -> str:
    """评审侧真实提供模型 id(``/v1/models`` 优先,拿不到才退回配置值)。"""
    section = review_config() if section is None else section
    base_url = str(section.get("base_url") or "")
    if not base_url:
        raise ReviewUnavailable("缺独立评审端点:config.review.base_url 未配置")
    try:
        ids = model_client.models(base_url=base_url, api_key=api_key(section), timeout=15.0)
    except ReviewUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - 端点不通要如实上报,不猜模型名
        raise ReviewUnavailable(f"独立评审端点探测失败:{type(exc).__name__}") from exc
    configured = str(section.get("model_id") or "")
    if ids and configured and configured not in ids:
        raise ReviewUnavailable(f"独立评审模型不在端点可用列表里:{configured} ∉ {','.join(ids)}")
    return ids[0] if ids else configured


def verdict_markers(text: str) -> dict[str, bool]:
    """按评审原文措辞判定阻断/许可;两类标记都没有 ⇒ 两者都 False(不给独立标签)。"""
    body = text or ""
    blocking = [marker for marker in BLOCKING_MARKERS if marker in body]
    publishable = [marker for marker in PUBLISHABLE_MARKERS if marker in body]
    return {
        "blocking": bool(blocking),
        "publishable": bool(publishable) and not blocking,
        "blocking_markers": blocking,
        "publishable_markers": publishable,
    }


def review(draft: str, section: dict | None = None) -> dict[str, Any]:
    """让评审模型只看「审评这篇文章 + 正文」;返回判定、模型 id 与原文,不含密钥。"""
    section = review_config() if section is None else section
    reviewer_id = served_model(section)
    prompt = f"{REVIEW_PROMPT}\n\n{(draft or '').strip()}"
    try:
        outcome = model_client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=int(section.get("max_tokens", 8000)),
            temperature=float(section.get("temperature", 0.0)),
            base_url=str(section["base_url"]),
            model_id=reviewer_id,
            api_key=api_key(section),
            timeout_s=float(section.get("request_timeout_s", 300)),
            enable_thinking=bool(section.get("enable_thinking", False)),
        )
    except model_client.ModelError as exc:
        raise ReviewUnavailable(f"独立评审调用失败:{exc}") from exc
    text = outcome.get("text") or ""
    verdict = verdict_markers(text)
    LOG.info(
        "writing.review.done",
        reviewer_model=outcome.get("model") or reviewer_id,
        prompt_chars=len(prompt),
        verdict_chars=len(text),
        finish_reason=outcome.get("finish_reason"),
        blocking=verdict["blocking"],
        publishable=verdict["publishable"],
        elapsed_s=outcome.get("elapsed_s"),
    )
    return {
        "reviewer_model": outcome.get("model") or reviewer_id,
        "verdict_text": text,
        "verdict": verdict,
        "prompt": REVIEW_PROMPT,
        "finish_reason": outcome.get("finish_reason"),
        "usage": outcome.get("usage"),
        "elapsed_s": outcome.get("elapsed_s"),
    }
