from app.services.identifiers import normalize_arxiv_id, normalize_doi, normalize_name, normalize_tag


def test_normalize_doi() -> None:
    assert normalize_doi("https://doi.org/10.1000/ABC.123") == "10.1000/abc.123"
    assert normalize_doi("DOI: 10.1/TEST") == "10.1/test"
    assert normalize_doi(None) is None


def test_normalize_arxiv_id() -> None:
    assert normalize_arxiv_id("https://arxiv.org/pdf/2401.01234.pdf") == "2401.01234"
    assert normalize_arxiv_id("arXiv: 2401.01234v2") == "2401.01234v2"


def test_normalize_names_and_tags() -> None:
    assert normalize_name("  Ada   Lovelace ") == "ada lovelace"
    assert normalize_tag("Dense_Retrieval") == "dense-retrieval"
