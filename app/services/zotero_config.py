from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from app.config import get_settings
from app.schemas import ZoteroSettingsInput, ZoteroSettingsOut


@dataclass(frozen=True)
class ZoteroConfig:
    enabled: bool = False
    api_key: str | None = None
    user_id: int | None = None
    username: str | None = None
    collection_name: str = "Paperlib"
    collection_key: str | None = None
    auto_push_discovered: bool = True
    last_library_version: int = 0


def _config_path() -> Path:
    return get_settings().zotero_config_path


def load_zotero_config() -> ZoteroConfig:
    path = _config_path()
    if not path.is_file():
        return ZoteroConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        fields = ZoteroConfig.__dataclass_fields__
        return ZoteroConfig(**{key: raw[key] for key in fields if key in raw})
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return ZoteroConfig()


def _write_config(config: ZoteroConfig) -> ZoteroConfig:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return config


def save_zotero_config(payload: ZoteroSettingsInput) -> ZoteroConfig:
    current = load_zotero_config()
    api_key = current.api_key
    if payload.clear_api_key:
        api_key = None
    elif payload.api_key and payload.api_key.strip():
        api_key = payload.api_key.strip()

    collection_changed = payload.collection_name != current.collection_name
    return _write_config(
        ZoteroConfig(
            enabled=payload.enabled,
            api_key=api_key,
            user_id=current.user_id if api_key else None,
            username=current.username if api_key else None,
            collection_name=payload.collection_name,
            collection_key=None if collection_changed else current.collection_key,
            auto_push_discovered=payload.auto_push_discovered,
            last_library_version=current.last_library_version if api_key else 0,
        )
    )


def update_zotero_runtime(config: ZoteroConfig, **changes: object) -> ZoteroConfig:
    return _write_config(replace(config, **changes))


def config_to_out(config: ZoteroConfig) -> ZoteroSettingsOut:
    hint = None
    if config.api_key:
        hint = f"••••{config.api_key[-4:]}" if len(config.api_key) >= 4 else "••••"
    return ZoteroSettingsOut(
        enabled=config.enabled,
        api_key_configured=bool(config.api_key),
        api_key_hint=hint,
        user_id=config.user_id,
        username=config.username,
        collection_name=config.collection_name,
        collection_key=config.collection_key,
        auto_push_discovered=config.auto_push_discovered,
        last_library_version=config.last_library_version,
    )
