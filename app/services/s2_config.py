from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.schemas import SemanticScholarSettingsInput, SemanticScholarSettingsOut


@dataclass(frozen=True)
class SemanticScholarConfig:
    api_key: str | None = None


def _config_path() -> Path:
    return get_settings().semantic_scholar_config_path


def load_s2_config() -> SemanticScholarConfig:
    path = _config_path()
    if not path.is_file():
        return SemanticScholarConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return SemanticScholarConfig(api_key=raw.get("api_key") or None)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return SemanticScholarConfig()


def save_s2_config(payload: SemanticScholarSettingsInput) -> SemanticScholarConfig:
    current = load_s2_config()
    api_key = current.api_key
    if payload.clear_api_key:
        api_key = None
    elif payload.api_key and payload.api_key.strip():
        api_key = payload.api_key.strip()
    config = SemanticScholarConfig(api_key=api_key)
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"api_key": api_key}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return config


def config_to_out(config: SemanticScholarConfig) -> SemanticScholarSettingsOut:
    hint = None
    if config.api_key:
        hint = f"••••{config.api_key[-4:]}" if len(config.api_key) >= 4 else "••••"
    return SemanticScholarSettingsOut(
        api_key_configured=bool(config.api_key),
        api_key_hint=hint,
    )
