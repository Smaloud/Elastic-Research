from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import get_settings
from app.schemas import LLMSettingsInput, LLMSettingsOut


@dataclass(frozen=True)
class LLMRuntimeConfig:
    enabled: bool = False
    provider: str = "ollama"
    base_url: str = "http://host.docker.internal:11434/v1"
    model: str = "qwen3:8b"
    api_key: str | None = None
    max_input_chars: int = 60000
    temperature: float = 0.1


def _config_path() -> Path:
    return get_settings().llm_config_path


def load_llm_config() -> LLMRuntimeConfig:
    path = _config_path()
    if not path.is_file():
        return LLMRuntimeConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return LLMRuntimeConfig(
            enabled=bool(raw.get("enabled", False)),
            provider=raw.get("provider", "ollama"),
            base_url=raw.get("base_url", "http://host.docker.internal:11434/v1"),
            model=raw.get("model", "qwen3:8b"),
            api_key=raw.get("api_key") or None,
            max_input_chars=int(raw.get("max_input_chars", 60000)),
            temperature=float(raw.get("temperature", 0.1)),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return LLMRuntimeConfig()


def save_llm_config(payload: LLMSettingsInput) -> LLMRuntimeConfig:
    current = load_llm_config()
    api_key = current.api_key
    if payload.clear_api_key:
        api_key = None
    elif payload.api_key and payload.api_key.strip():
        api_key = payload.api_key.strip()

    config = LLMRuntimeConfig(
        enabled=payload.enabled,
        provider=payload.provider,
        base_url=payload.base_url,
        model=payload.model,
        api_key=api_key,
        max_input_chars=payload.max_input_chars,
        temperature=payload.temperature,
    )
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return config


def config_to_out(config: LLMRuntimeConfig) -> LLMSettingsOut:
    hint = None
    if config.api_key:
        hint = f"••••{config.api_key[-4:]}" if len(config.api_key) >= 4 else "••••"
    return LLMSettingsOut(
        enabled=config.enabled,
        provider=config.provider,
        base_url=config.base_url,
        model=config.model,
        api_key_configured=bool(config.api_key),
        api_key_hint=hint,
        max_input_chars=config.max_input_chars,
        temperature=config.temperature,
    )
