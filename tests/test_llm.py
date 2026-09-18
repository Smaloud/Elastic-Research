import json

import pytest

from app.schemas import LLMSettingsInput
from app.services import llm_config
from app.services.extraction import _json_from_reply
from app.services.llm_client import LLMUnavailable


def test_llm_config_round_trip_masks_api_key(tmp_path, monkeypatch):
    config_path = tmp_path / "private" / "llm.json"
    monkeypatch.setattr(llm_config, "_config_path", lambda: config_path)

    saved = llm_config.save_llm_config(
        LLMSettingsInput(
            enabled=True,
            provider="openai_compatible",
            base_url="https://openrouter.ai/api/v1/",
            model="qwen/test",
            api_key="secret-1234",
        )
    )

    assert saved.api_key == "secret-1234"
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert json.loads(config_path.read_text())["api_key"] == "secret-1234"
    public = llm_config.config_to_out(llm_config.load_llm_config())
    assert public.api_key_configured is True
    assert public.api_key_hint == "••••1234"
    assert "secret" not in public.model_dump_json()


def test_blank_key_preserves_existing_key(tmp_path, monkeypatch):
    config_path = tmp_path / "llm.json"
    monkeypatch.setattr(llm_config, "_config_path", lambda: config_path)
    first = LLMSettingsInput(api_key="existing-key")
    llm_config.save_llm_config(first)

    second = LLMSettingsInput(api_key=None, model="another-model")
    assert llm_config.save_llm_config(second).api_key == "existing-key"


def test_extraction_accepts_fenced_json():
    result = _json_from_reply('```json\n{"tags": ["RAG"], "methods": []}\n```')
    assert result["tags"] == ["RAG"]


def test_extraction_rejects_non_json():
    with pytest.raises(LLMUnavailable):
        _json_from_reply("I cannot comply")
