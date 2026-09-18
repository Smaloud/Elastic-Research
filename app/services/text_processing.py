import re


def clean_extracted_text(value: str) -> str:
    value = value.replace("\x00", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def chunk_text(value: str, max_chars: int = 2200, overlap: int = 240) -> list[str]:
    """Split text on nearby sentence/line boundaries while retaining small overlap."""
    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")
    if overlap < 0 or overlap >= max_chars // 2:
        raise ValueError("overlap must be non-negative and less than half max_chars")

    text = clean_extracted_text(value)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        hard_end = min(start + max_chars, length)
        end = hard_end
        if hard_end < length:
            floor = start + int(max_chars * 0.6)
            candidates = [
                text.rfind("\n\n", floor, hard_end),
                text.rfind(". ", floor, hard_end),
                text.rfind("。", floor, hard_end),
                text.rfind("; ", floor, hard_end),
            ]
            boundary = max(candidates)
            if boundary >= floor:
                end = boundary + (1 if text[boundary] in ".。" else 0)

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        next_start = max(end - overlap, start + 1)
        start = next_start

    return chunks


def make_snippet(value: str, query: str, max_chars: int = 420) -> str:
    text = clean_extracted_text(value)
    if len(text) <= max_chars:
        return text

    terms = [term.casefold() for term in re.findall(r"[\w-]{2,}", query)]
    lowered = text.casefold()
    positions = [lowered.find(term) for term in terms]
    positions = [position for position in positions if position >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - max_chars // 3)
    end = min(len(text), start + max_chars)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"
