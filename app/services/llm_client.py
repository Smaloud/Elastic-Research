from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.services.llm_config import LLMRuntimeConfig


class LLMUnavailable(RuntimeError):
    pass


def _headers(config: LLMRuntimeConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    if "openrouter.ai" in config.base_url:
        headers["HTTP-Referer"] = "http://localhost:8765"
        headers["X-Title"] = "Paperlib"
    return headers


def chat(config: LLMRuntimeConfig, messages: list[dict[str, str]], timeout: float = 180) -> str:
    if not config.enabled:
        raise LLMUnavailable("LLM 尚未启用，请先在“LLM 设置”中保存并启用配置")
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "stream": False,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, headers=_headers(config), json=payload)
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise LLMUnavailable("模型返回了空内容")
        return content.strip()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:800]
        raise LLMUnavailable(
            f"模型接口返回 {exc.response.status_code}：{detail}"
        ) from exc
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LLMUnavailable(f"无法连接或解析模型接口：{exc}") from exc


def test_connection(config: LLMRuntimeConfig) -> tuple[str, int]:
    started = time.monotonic()
    reply = chat(
        config,
        [
            {"role": "system", "content": "You are a connection test. Reply with only OK."},
            {"role": "user", "content": "Reply OK."},
        ],
        timeout=45,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    return reply[:100], latency_ms
