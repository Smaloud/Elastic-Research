import json

import pytest

from app.schemas import LLMSettingsInput
from app.services import llm_config
from app.services.extraction import (
    SYSTEM_PROMPT,
    _json_from_reply,
    _normalize_category,
    _normalize_item,
)
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


def test_taxonomy_normalizes_legacy_category_and_keeps_typed_attributes():
    source = "[第7页]\nWe train BERT on WikiText-103 with a batch size of 32."
    item = _normalize_item(
        {
            "category": "data",
            "subtype": "training_corpus",
            "name": "WikiText-103",
            "description": "Training corpus",
            "attributes": {"usage": "training", "batch_size": 32},
            "page": 7,
            "evidence": "We train BERT on WikiText-103",
            "confidence": 0.93,
        },
        source,
    )

    assert _normalize_category("data") == "dataset"
    assert item is not None
    category, value, evidence, confidence = item
    assert category == "dataset"
    assert value["category_label"] == "数据集/语料"
    assert value["subtype"] == "training_corpus"
    assert value["attributes"]["usage"] == "training"
    assert value["page"] == 7
    assert evidence == "We train BERT on WikiText-103"
    assert confidence == pytest.approx(0.93)


def test_taxonomy_rejects_unknown_category_or_ungrounded_evidence():
    source = "[第2页]\nAccuracy is 91.2 on the test split."
    common = {
        "name": "Accuracy",
        "description": "Reported test result",
        "attributes": {"value": 91.2, "unit": "%"},
        "page": 2,
        "confidence": 0.9,
    }

    assert _normalize_item({**common, "category": "unknown", "evidence": "Accuracy is 91.2"}, source) is None
    assert _normalize_item({**common, "category": "experimental_result", "evidence": "Accuracy is 99.9"}, source) is None
    assert '"evidence": "原文短句"' in SYSTEM_PROMPT
