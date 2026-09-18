import re
import unicodedata


DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
ARXIV_PREFIX_RE = re.compile(r"^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)", re.IGNORECASE)


def normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return normalize_whitespace(normalized).casefold()


def normalize_tag(value: str) -> str:
    return normalize_name(value).replace("_", "-")


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = DOI_PREFIX_RE.sub("", value.strip()).strip().lower()
    return normalized or None


def normalize_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = ARXIV_PREFIX_RE.sub("", value.strip()).strip()
    normalized = re.sub(r"\.pdf$", "", normalized, flags=re.IGNORECASE)
    return normalized or None
