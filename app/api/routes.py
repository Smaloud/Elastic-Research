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
    S2ExpandInput,
    S2ImportInput,
    S2ImportOut,
    S2IntersectionInput,
    S2PaperResults,
    SearchMode,
    SearchResponse,
    SemanticScholarSettingsInput,
    SemanticScholarSettingsOut,
    StateInput,
    StatePatch,
    WorkCreate,
    WorkAnalysisOut,
    WorkOut,
    ZoteroSettingsInput,
    ZoteroSettingsOut,
    ZoteroSyncOut,
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
from app.services.s2_config import (
    config_to_out as s2_config_to_out,
    load_s2_config,
    save_s2_config,
)
from app.services.semantic_scholar import (
    SemanticScholarUnavailable,
    get_paper as get_s2_paper,
    import_paper as import_s2_paper,
    intersect_citations,
    related_papers,
    search_papers,
)
from app.services.serialization import analysis_to_out, fact_to_out, work_to_out
from app.services.zotero_config import (
    config_to_out as zotero_config_to_out,
    load_zotero_config,
    save_zotero_config,
)
from app.services.zotero_sync import (
    ZoteroUnavailable,
    push_work_if_enabled,
    sync_library as sync_zotero_library,
    verify_connection as verify_zotero_connection,
)


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
    result.warnings.extend(push_work_if_enabled(session, result.work))
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

    warnings.extend(push_work_if_enabled(session, result.work))
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


@router.get("/s2/settings", response_model=SemanticScholarSettingsOut)
def get_s2_settings() -> SemanticScholarSettingsOut:
    return s2_config_to_out(load_s2_config())


@router.put("/s2/settings", response_model=SemanticScholarSettingsOut)
def update_s2_settings(
    payload: SemanticScholarSettingsInput,
) -> SemanticScholarSettingsOut:
    return s2_config_to_out(save_s2_config(payload))


@router.post("/s2/test")
def test_s2_connection(session: Session = Depends(get_db)) -> dict[str, object]:
    try:
        papers = search_papers(session, "SciBERT", 1)
    except SemanticScholarUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "ok": True,
        "message": "连接成功",
        "authenticated": bool(load_s2_config().api_key),
        "sample_title": papers[0].title if papers else None,
    }


@router.get("/s2/search", response_model=S2PaperResults)
def search_semantic_scholar(
    q: str = Query(..., min_length=2, max_length=500),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db),
) -> S2PaperResults:
    try:
        papers = search_papers(session, q, limit)
    except SemanticScholarUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return S2PaperResults(query=q, total=len(papers), papers=papers)


@router.post("/s2/discover/expand", response_model=S2PaperResults)
def expand_semantic_scholar(
    payload: S2ExpandInput,
    session: Session = Depends(get_db),
) -> S2PaperResults:
    work = _get_work_or_404(session, payload.seed_work_id)
    try:
        papers = related_papers(
            session, work, payload.direction, payload.limit
        )
    except SemanticScholarUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    label = "引用它的论文" if payload.direction == "citations" else "它引用的论文"
    return S2PaperResults(
        query=f"《{work.title}》· {label}", total=len(papers), papers=papers
    )


@router.post("/s2/discover/intersection", response_model=S2PaperResults)
def discover_semantic_scholar_intersection(
    payload: S2IntersectionInput,
    session: Session = Depends(get_db),
) -> S2PaperResults:
    works: list[models.Work] = []
    for work_id in payload.seed_work_ids:
        work = session.get(models.Work, work_id)
        if work is None:
            raise HTTPException(status_code=404, detail=f"未找到种子论文：{work_id}")
        works.append(work)
    try:
        papers = intersect_citations(
            session, works, payload.limit_per_seed, payload.year_from
        )
    except SemanticScholarUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return S2PaperResults(
        query="同时引用全部种子论文",
        total=len(papers),
        papers=papers,
        warnings=(
            ["结果受每篇种子的抓取上限影响；高被引论文可提高“每篇最多获取”后重试。"]
            if any((work.source_metadata or {}).get("semantic_scholar") is None for work in works)
            else []
        ),
    )


@router.post("/s2/import", response_model=S2ImportOut)
def import_semantic_scholar_paper(
    payload: S2ImportInput,
    session: Session = Depends(get_db),
) -> S2ImportOut:
    seeds: list[models.Work] = []
    for work_id in payload.seed_work_ids:
        seeds.append(_get_work_or_404(session, work_id))
    try:
        paper = get_s2_paper(payload.paper_id)
        work, created, edges, warnings = import_s2_paper(
            session, paper, seeds, payload.relation
        )
    except SemanticScholarUnavailable as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DuplicateWorkError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    warnings.extend(push_work_if_enabled(session, work))
    return S2ImportOut(
        work=work_to_out(session, work),
        created=created,
        citation_edges_added=edges,
        warnings=warnings,
    )


@router.get("/zotero/settings", response_model=ZoteroSettingsOut)
def get_zotero_settings() -> ZoteroSettingsOut:
    return zotero_config_to_out(load_zotero_config())


@router.put("/zotero/settings", response_model=ZoteroSettingsOut)
def update_zotero_settings(payload: ZoteroSettingsInput) -> ZoteroSettingsOut:
    return zotero_config_to_out(save_zotero_config(payload))


@router.post("/zotero/test", response_model=ZoteroSettingsOut)
def test_zotero_connection() -> ZoteroSettingsOut:
    try:
        return zotero_config_to_out(verify_zotero_connection())
    except ZoteroUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/zotero/sync", response_model=ZoteroSyncOut)
def run_zotero_sync(session: Session = Depends(get_db)) -> ZoteroSyncOut:
    try:
        return sync_zotero_library(session)
    except ZoteroUnavailable as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
