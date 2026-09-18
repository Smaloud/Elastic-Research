from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings
from app.schemas import StatePatch, WorkCreate
from app.services.embeddings import EmbeddingUnavailable, get_embedder
from app.services.identifiers import (
    normalize_arxiv_id,
    normalize_doi,
    normalize_name,
    normalize_tag,
)
from app.services.text_processing import chunk_text, clean_extracted_text


class DuplicateWorkError(ValueError):
    pass


class DuplicateDocumentError(ValueError):
    pass


@dataclass
class IngestResult:
    work: models.Work
    warnings: list[str]


def _check_duplicate_identifiers(session: Session, doi: str | None, arxiv_id: str | None) -> None:
    clauses = []
    if doi:
        clauses.append(models.Work.doi == doi)
    if arxiv_id:
        clauses.append(models.Work.arxiv_id == arxiv_id)
    for clause in clauses:
        duplicate = session.scalar(select(models.Work.id).where(clause).limit(1))
        if duplicate:
            raise DuplicateWorkError(f"该 DOI/arXiv 记录已存在：{duplicate}")


def _attach_authors(session: Session, work: models.Work, names: list[str]) -> None:
    for position, name in enumerate(names):
        normalized = normalize_name(name)
        author = session.scalar(select(models.Author).where(models.Author.normalized_name == normalized))
        if author is None:
            author = models.Author(name=name.strip(), normalized_name=normalized)
            session.add(author)
            session.flush()
        work.authors.append(
            models.WorkAuthor(author=author, position=position)
        )


def _attach_tags(session: Session, work: models.Work, names: list[str]) -> None:
    for name in names:
        normalized = normalize_tag(name)
        tag = session.scalar(select(models.Tag).where(models.Tag.normalized_name == normalized))
        if tag is None:
            tag = models.Tag(name=name.strip(), normalized_name=normalized)
            session.add(tag)
            session.flush()
        work.tags.append(models.WorkTag(tag=tag, source="human", confidence=1.0))


def _embed_sections(sections: list[models.Section], warnings: list[str]) -> None:
    if not sections:
        return
    texts = [f"{section.title or ''}\n{section.text}".strip() for section in sections]
    try:
        vectors = get_embedder().embed_documents(texts)
    except EmbeddingUnavailable as exc:
        warnings.append(str(exc))
        return
    model_name = get_settings().embedding_model
    for section, vector in zip(sections, vectors, strict=True):
        section.embedding = vector
        section.embedding_model = model_name


def create_work(session: Session, payload: WorkCreate) -> IngestResult:
    doi = normalize_doi(payload.doi)
    arxiv_id = normalize_arxiv_id(payload.arxiv_id)
    _check_duplicate_identifiers(session, doi, arxiv_id)

    year = payload.publication_year
    if year is None and payload.publication_date is not None:
        year = payload.publication_date.year

    work = models.Work(
        kind=payload.kind,
        title=payload.title,
        abstract=payload.abstract.strip() if payload.abstract else None,
        publication_date=payload.publication_date,
        publication_year=year,
        venue=payload.venue.strip() if payload.venue else None,
        doi=doi,
        arxiv_id=arxiv_id,
        semantic_scholar_id=(
            payload.semantic_scholar_id.strip() if payload.semantic_scholar_id else None
        ),
        language=payload.language.strip() if payload.language else None,
        source_metadata={"source": "manual"},
    )
    session.add(work)
    session.flush()
    _attach_authors(session, work, payload.authors)
    _attach_tags(session, work, payload.tags)
    work.state = models.UserWorkState(
        importance=payload.state.importance,
        familiarity=payload.state.familiarity,
        reading_status=payload.state.reading_status,
        notes=payload.state.notes,
    )

    initial_text = (
        f"{work.title}\n\n{work.abstract}" if work.abstract else work.title
    )
    section = models.Section(
        work=work,
        kind="abstract" if work.abstract else "metadata",
        title="Abstract" if work.abstract else "Title",
        ordinal=0,
        text=initial_text,
        section_metadata={"source": "manual"},
    )
    session.add(section)
    warnings: list[str] = []
    _embed_sections([section], warnings)
    session.commit()
    session.refresh(work)
    return IngestResult(work=work, warnings=warnings)


def _save_upload(upload: UploadFile) -> tuple[Path, str, int]:
    settings = get_settings()
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    temporary = settings.storage_path / f".{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    with temporary.open("wb") as output:
        while chunk := upload.file.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
            size += len(chunk)
    sha256 = digest.hexdigest()
    final_path = settings.storage_path / f"{sha256}.pdf"
    if final_path.exists():
        temporary.unlink(missing_ok=True)
    else:
        os.replace(temporary, final_path)
    return final_path, sha256, size


def attach_pdf(session: Session, work: models.Work, upload: UploadFile) -> list[str]:
    warnings: list[str] = []
    if upload.content_type not in {"application/pdf", "application/octet-stream", None}:
        raise ValueError("首版仅支持 PDF 文件")

    path, sha256, _ = _save_upload(upload)
    duplicate = session.scalar(select(models.Document).where(models.Document.sha256 == sha256))
    if duplicate:
        raise DuplicateDocumentError(f"该 PDF 已导入到记录：{duplicate.work_id}")

    document = models.Document(
        work=work,
        source_kind="upload",
        original_filename=upload.filename,
        relative_path=path.name,
        sha256=sha256,
        mime_type="application/pdf",
        parse_status="processing",
    )
    session.add(document)
    session.flush()

    sections: list[models.Section] = []
    try:
        reader = PdfReader(str(path))
        document.page_count = len(reader.pages)
        ordinal = 1
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = clean_extracted_text(page.extract_text() or "")
            for part_number, part in enumerate(chunk_text(page_text), start=1):
                section = models.Section(
                    work=work,
                    document=document,
                    kind="page_chunk",
                    title=f"Page {page_number} · {part_number}",
                    ordinal=ordinal,
                    page_start=page_number,
                    page_end=page_number,
                    text=part,
                    section_metadata={"parser": "pypdf"},
                )
                ordinal += 1
                session.add(section)
                sections.append(section)
        if sections:
            document.parse_status = "parsed"
            _embed_sections(sections, warnings)
        else:
            document.parse_status = "needs_ocr"
            document.parse_error = "PDF 未提取到文本；后续需要 OCR/GROBID 处理"
            warnings.append(document.parse_error)
    except Exception as exc:
        document.parse_status = "failed"
        document.parse_error = str(exc)[:2000]
        warnings.append(f"PDF 文本提取失败：{exc}")
    session.commit()
    session.refresh(work)
    return warnings


def patch_state(session: Session, work: models.Work, patch: StatePatch) -> models.Work:
    state = work.state
    if state is None:
        state = models.UserWorkState(work=work)
        session.add(state)
    updates = patch.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(state, field, value)
    session.commit()
    session.refresh(work)
    return work


def reindex_embeddings(session: Session, batch_size: int = 64) -> tuple[int, list[str]]:
    """Embed missing or stale sections in bounded batches."""
    model_name = get_settings().embedding_model
    sections = list(
        session.scalars(
            select(models.Section)
            .where(
                (models.Section.embedding.is_(None))
                | (models.Section.embedding_model.is_distinct_from(model_name))
            )
            .order_by(models.Section.created_at.asc())
        )
    )
    completed = 0
    warnings: list[str] = []
    for offset in range(0, len(sections), batch_size):
        batch = sections[offset : offset + batch_size]
        batch_warnings: list[str] = []
        _embed_sections(batch, batch_warnings)
        warnings.extend(batch_warnings)
        if batch_warnings:
            session.rollback()
            break
        completed += len(batch)
        session.commit()
    return completed, warnings
