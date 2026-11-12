"""Strata OpenAI 兼容客户端(仅标准库;模型自动发现;无密钥也可用)。"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

from ttxt import config as cfg

_MODEL_CACHE: list[str] = []


class ModelError(RuntimeError):
    pass


def _config() -> dict:
    return cfg.load_config().get("strata", {})


def _base_url(base_url: str | None = None) -> str:
    if base_url:
        return base_url.rstrip("/")
    return str(_config().get("base_url", "http://127.0.0.1:8080")).rstrip("/")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    env_name = str(_config().get("api_key_env", "STRATA_API_KEY"))
    key = os.environ.get(env_name)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _get_json(path: str, base_url: str | None, timeout: float) -> dict | None:
    request = urllib.request.Request(_base_url(base_url) + path, headers=_headers())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 探测失败返回 None
        return None


def health(base_url: str | None = None, timeout: float = 3.0) -> dict | None:
    return _get_json("/health", base_url, timeout)


def models(base_url: str | None = None, timeout: float = 5.0) -> list[str]:
    payload = _get_json("/v1/models", base_url, timeout) or {}
    return [item["id"] for item in payload.get("data", []) if item.get("id")]


def wait_ready(
    base_url: str | None = None,
    timeout_s: float = 180.0,
    poll_s: float = 2.0,
) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict | None = None
    while time.monotonic() < deadline:
        data = health(base_url, timeout=5.0)
        if data and data.get("loaded"):
            return data
        last = data
        time.sleep(poll_s)
    raise ModelError(f"model not ready within {timeout_s}s (last health: {last})")


def resolve_model(base_url: str | None = None) -> str:
    global _MODEL_CACHE
    if _MODEL_CACHE:
        return _MODEL_CACHE[0]
    ids = models(base_url)
    if not ids:
        raise ModelError("no model available from /v1/models")
    _MODEL_CACHE = ids
    return ids[0]


def _post_chat(payload: dict, base_url: str | None, timeout_s: float) -> dict:
    request = urllib.request.Request(
        _base_url(base_url) + "/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def chat(
    messages: list[dict],
    max_tokens: int = 256,
    temperature: float = 0.2,
    base_url: str | None = None,
    timeout_s: float | None = None,
    model_id: str | None = None,
    enable_thinking: bool | None = None,
) -> dict:
    global _MODEL_CACHE
    if timeout_s is None:
        timeout_s = float(_config().get("request_timeout_s", 600))
    if enable_thinking is None:
        enable_thinking = bool(_config().get("enable_thinking_default", False))
    payload: dict[str, Any] = {
        "model": model_id or resolve_model(base_url),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    started = time.monotonic()
    try:
        body = _post_chat(payload, base_url, timeout_s)
    except Exception as exc:  # noqa: BLE001 - 模型可能已切换,刷新后重试一次
        if model_id is not None:
            raise ModelError(f"chat failed: {exc}") from exc
        _MODEL_CACHE = []
        payload["model"] = resolve_model(base_url)
        try:
            body = _post_chat(payload, base_url, timeout_s)
        except Exception as retry_exc:  # noqa: BLE001
            raise ModelError(f"chat failed: {retry_exc}") from retry_exc
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content", "") or ""
    if not text.strip():
        raise ModelError(f"empty completion (finish_reason={choice.get('finish_reason')})")
    return {
        "model": body.get("model", payload["model"]),
        "text": text,
        "reasoning": (message.get("reasoning_content") or "")[:4000],
        "finish_reason": choice.get("finish_reason"),
        "usage": body.get("usage"),
        "elapsed_s": round(time.monotonic() - started, 3),
    }
