from app.schemas import SemanticScholarSettingsInput
from app.services import s2_config
from app.services.semantic_scholar import _candidate


def test_semantic_scholar_config_masks_key(tmp_path, monkeypatch):
    path = tmp_path / "semantic_scholar.json"
    monkeypatch.setattr(s2_config, "_config_path", lambda: path)

    saved = s2_config.save_s2_config(
        SemanticScholarSettingsInput(api_key="private-key-9876")
    )

    assert saved.api_key == "private-key-9876"
    assert path.stat().st_mode & 0o777 == 0o600
    public = s2_config.config_to_out(s2_config.load_s2_config())
    assert public.api_key_configured is True
    assert public.api_key_hint == "••••9876"
    assert "private-key" not in public.model_dump_json()


def test_candidate_maps_external_ids_and_pdf():
    paper = _candidate(
        {
            "paperId": "abc123",
            "corpusId": 42,
            "title": "A useful paper",
            "authors": [{"name": "Ada Lovelace"}],
            "year": 2026,
            "publicationDate": "2026-04-05",
            "externalIds": {
                "DOI": "https://doi.org/10.1000/Example",
                "ArXiv": "arXiv:2604.00001",
            },
            "openAccessPdf": {"url": "https://example.org/paper.pdf"},
        }
    )

    assert paper is not None
    assert paper.paper_id == "abc123"
    assert paper.authors == ["Ada Lovelace"]
    assert paper.doi == "10.1000/example"
    assert paper.arxiv_id == "2604.00001"
    assert paper.publication_date.isoformat() == "2026-04-05"
    assert paper.open_access_pdf_url == "https://example.org/paper.pdf"


def test_candidate_rejects_incomplete_record():
    assert _candidate({"paperId": "abc"}) is None
