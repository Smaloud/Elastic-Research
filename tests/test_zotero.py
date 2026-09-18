import json
import uuid

from app import models
from app.schemas import ZoteroSettingsInput
from app.services import zotero_config, zotero_sync


def test_zotero_config_round_trip_masks_key(tmp_path, monkeypatch):
    config_path = tmp_path / "private" / "zotero.json"
    monkeypatch.setattr(zotero_config, "_config_path", lambda: config_path)

    saved = zotero_config.save_zotero_config(
        ZoteroSettingsInput(
            enabled=True,
            api_key="zotero-secret-1234",
            collection_name="Paperlib Research",
            auto_push_discovered=True,
        )
    )

    assert saved.api_key == "zotero-secret-1234"
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert json.loads(config_path.read_text())["api_key"] == "zotero-secret-1234"
    public = zotero_config.config_to_out(zotero_config.load_zotero_config())
    assert public.api_key_configured is True
    assert public.api_key_hint == "••••1234"
    assert "secret" not in public.model_dump_json()


def test_zotero_mapping_preserves_identifiers_and_tags():
    work = models.Work(
        id=uuid.uuid4(),
        kind="paper",
        title="A Retrieval Paper",
        abstract="Evidence-grounded retrieval.",
        publication_year=2026,
        venue="Test Journal",
        doi="10.1000/test",
        arxiv_id="2601.01234",
        language="en",
        source_metadata={"semantic_scholar": {"url": "https://example.org/paper"}},
    )
    author = models.Author(name="Ada Lovelace", normalized_name="ada lovelace")
    tag = models.Tag(name="retrieval", normalized_name="retrieval")
    work.authors.append(models.WorkAuthor(author=author, position=0))
    work.tags.append(models.WorkTag(tag=tag, source="human", confidence=1.0))

    item = zotero_sync._zotero_item(work, "COLL1234")

    assert item["itemType"] == "journalArticle"
    assert item["title"] == work.title
    assert item["DOI"] == "10.1000/test"
    assert item["publicationTitle"] == "Test Journal"
    assert item["collections"] == ["COLL1234"]
    assert {entry["tag"] for entry in item["tags"]} == {"retrieval", "Paperlib"}
    assert "arXiv: 2601.01234" in item["extra"]


def test_zotero_parses_year_and_arxiv():
    assert zotero_sync._year("2024-08-17") == 2024
    assert zotero_sync._year("forthcoming") is None
    assert (
        zotero_sync._arxiv_id({"extra": "arXiv: 2401.12345v2"})
        == "2401.12345v2"
    )
