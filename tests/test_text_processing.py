import pytest

from app.services.text_processing import chunk_text, clean_extracted_text, make_snippet


def test_clean_extracted_text() -> None:
    assert clean_extracted_text("a   b\n\n\n c\x00") == "a b\n\nc"


def test_chunk_text_splits_and_keeps_content() -> None:
    text = ("A useful sentence about retrieval. " * 80).strip()
    chunks = chunk_text(text, max_chars=300, overlap=40)
    assert len(chunks) > 2
    assert all(1 <= len(chunk) <= 300 for chunk in chunks)
    assert "retrieval" in chunks[-1]


def test_chunk_text_rejects_bad_overlap() -> None:
    with pytest.raises(ValueError):
        chunk_text("hello", max_chars=200, overlap=100)


def test_make_snippet_centers_query() -> None:
    text = "start " + ("padding " * 100) + "needle appears here " + ("tail " * 100)
    snippet = make_snippet(text, "needle", max_chars=120)
    assert "needle" in snippet
    assert len(snippet) <= 122
