from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings
from app.database import get_db
from app.schemas import (
    FactReviewInput,
    IngestResponse,
    LLMSettingsInput,
    LLMSettingsOut,
    LLMTestOut,
    SearchMode,
    SearchResponse,
    StateInput,
    StatePatch,
    WorkCreate,
    WorkAnalysisOut,
    WorkOut,
)
from app.services.embeddings import EmbeddingUnavailable
from app.services.extraction import apply_fact_review, extract_work
from app.services.library import (
    DuplicateDocumentError,
    DuplicateWorkError,
    attach_pdf,
    create_work,
    patch_state,
    reindex_embeddings,
)
from app.services.search import search_library
from app.services.llm_client import LLMUnavailable, test_connection
from app.services.llm_config import config_to_out, load_llm_config, save_llm_config
from app.services.serialization import analysis_to_out, fact_to_out, work_to_out


router = APIRouter(prefix="/api/v1")


def _get_work_or_404(session: Session, work_id: uuid.UUID) -> models.Work:
    work = session.get(models.Work, work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="未找到该记录")
    return work


def _split_list(value: str) -> list[str]:
    normalized = value.replace("；", ";").replace("，", ",").replace("\n", ",")
    parts: list[str] = []
    for semicolon_group in normalized.split(";"):
        parts.extend(semicolon_group.split(","))
    return [part.strip() for part in parts if part.strip()]


@router.get("/health")
def health(session: Session = Depends(get_db)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post("/works", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
def add_work(payload: WorkCreate, session: Session = Depends(get_db)) -> IngestResponse:
    try:
        result = create_work(session, payload)
    except DuplicateWorkError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="记录与已有 DOI/arXiv ID 冲突") from exc
    return IngestResponse(work=work_to_out(session, result.work), warnings=result.warnings)


@router.post(
    "/works/upload",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_work(
    title: str = Form(...),
    kind: str = Form("paper"),
    abstract: str | None = Form(None),
    authors: str = Form(""),
    tags: str = Form(""),
    publication_date: date | None = Form(None),
    publication_year: int | None = Form(None),
    venue: str | None = Form(None),
    doi: str | None = Form(None),
    arxiv_id: str | None = Form(None),
    language: str | None = Form(None),
    importance: int = Form(0),
    familiarity: int = Form(0),
    reading_status: str = Form("unread"),
    notes: str | None = Form(None),
    file: UploadFile | None = File(None),
    session: Session = Depends(get_db),
) -> IngestResponse:
    try:
        payload = WorkCreate(
            title=title,
            kind=kind,
            abstract=abstract,
            authors=_split_list(authors),
            tags=_split_list(tags),
            publication_date=publication_date,
            publication_year=publication_year,
            venue=venue,
            doi=doi,
            arxiv_id=arxiv_id,
            language=language,
            state=StateInput(
                importance=importance,
                familiarity=familiarity,
                reading_status=reading_status,
                notes=notes,
            ),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    try:
        result = create_work(session, payload)
        warnings = list(result.warnings)
        if file and file.filename:
            warnings.extend(attach_pdf(session, result.work, file))
    except (DuplicateWorkError, DuplicateDocumentError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="记录与已有标识符冲突") from exc

    session.refresh(result.work)
    return IngestResponse(work=work_to_out(session, result.work), warnings=warnings)


@router.get("/works/{work_id}", response_model=WorkOut)
def get_work(work_id: uuid.UUID, session: Session = Depends(get_db)) -> WorkOut:
    return work_to_out(session, _get_work_or_404(session, work_id))


@router.patch("/works/{work_id}/state", response_model=WorkOut)
def update_state(
    work_id: uuid.UUID,
    payload: StatePatch,
    session: Session = Depends(get_db),
) -> WorkOut:
    work = _get_work_or_404(session, work_id)
    return work_to_out(session, patch_state(session, work, payload))


@router.get("/documents/{document_id}/file")
def get_document_file(
    document_id: uuid.UUID, session: Session = Depends(get_db)
) -> FileResponse:
    document = session.get(models.Document, document_id)
    if document is None or not document.relative_path:
        raise HTTPException(status_code=404, detail="未找到该文件")
    storage_path = get_settings().storage_path.resolve()
    file_path = (storage_path / document.relative_path).resolve()
    if file_path.parent != storage_path or not file_path.is_file():
        raise HTTPException(status_code=404, detail="本地文件不存在")
    return FileResponse(
        file_path,
        media_type=document.mime_type or "application/octet-stream",
        filename=document.original_filename or file_path.name,
    )


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query("", max_length=1000),
    mode: SearchMode = Query("hybrid"),
    limit: int = Query(20, ge=1, le=100),
    year_from: int | None = Query(None, ge=1000, le=3000),
    year_to: int | None = Query(None, ge=1000, le=3000),
    tag: str | None = Query(None, max_length=200),
    min_importance: int | None = Query(None, ge=0, le=5),
    max_familiarity: int | None = Query(None, ge=0, le=5),
    session: Session = Depends(get_db),
) -> SearchResponse:
    if year_from and year_to and year_from > year_to:
        raise HTTPException(status_code=422, detail="year_from 不能大于 year_to")
    try:
        return search_library(
            session,
            query=q,
            mode=mode,
            limit=limit,
            year_from=year_from,
            year_to=year_to,
            tag=tag,
            min_importance=min_importance,
            max_familiarity=max_familiarity,
        )
    except EmbeddingUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/maintenance/reindex-embeddings")
def rebuild_embeddings(session: Session = Depends(get_db)) -> dict[str, object]:
    completed, warnings = reindex_embeddings(session)
    return {"embedded_sections": completed, "warnings": warnings}


@router.get("/llm/settings", response_model=LLMSettingsOut)
def get_llm_settings() -> LLMSettingsOut:
    return config_to_out(load_llm_config())


@router.put("/llm/settings", response_model=LLMSettingsOut)
def update_llm_settings(payload: LLMSettingsInput) -> LLMSettingsOut:
    return config_to_out(save_llm_config(payload))


@router.post("/llm/test", response_model=LLMTestOut)
def test_llm() -> LLMTestOut:
    config = load_llm_config()
    try:
        reply, latency_ms = test_connection(config)
    except LLMUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return LLMTestOut(
        ok=True,
        message=f"连接成功，模型回复：{reply}",
        model=config.model,
        latency_ms=latency_ms,
    )


@router.post("/works/{work_id}/extract", response_model=WorkAnalysisOut)
def run_llm_extraction(
    work_id: uuid.UUID, session: Session = Depends(get_db)
) -> WorkAnalysisOut:
    work = _get_work_or_404(session, work_id)
    try:
        warnings = extract_work(session, work)
    except LLMUnavailable as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return analysis_to_out(session, work, warnings)


@router.get("/works/{work_id}/analysis", response_model=WorkAnalysisOut)
def get_work_analysis(
    work_id: uuid.UUID, session: Session = Depends(get_db)
) -> WorkAnalysisOut:
    return analysis_to_out(session, _get_work_or_404(session, work_id))


@router.patch("/facts/{fact_id}/review")
def review_fact(
    fact_id: uuid.UUID,
    payload: FactReviewInput,
    session: Session = Depends(get_db),
):
    fact = session.get(models.StructuredFact, fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="未找到该提取项")
    apply_fact_review(session, fact, payload.status)
    session.refresh(fact)
    return fact_to_out(fact)
