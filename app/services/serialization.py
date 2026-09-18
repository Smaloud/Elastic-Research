from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from app.schemas import DocumentOut, StateOut, StructuredFactOut, WorkAnalysisOut, WorkOut


def work_to_out(session: Session, work: models.Work) -> WorkOut:
    state = work.state or models.UserWorkState(
        work_id=work.id,
        importance=0,
        familiarity=0,
        reading_status="unread",
    )
    counts = session.execute(
        select(
            func.count(models.Section.id),
            func.count(models.Section.embedding),
        ).where(models.Section.work_id == work.id)
    ).one()
    figure_count = session.scalar(
        select(func.count(models.Figure.id)).where(models.Figure.work_id == work.id)
    )
    return WorkOut(
        id=work.id,
        kind=work.kind,
        title=work.title,
        abstract=work.abstract,
        authors=[link.author.name for link in sorted(work.authors, key=lambda item: item.position)],
        tags=sorted(link.tag.name for link in work.tags),
        publication_date=work.publication_date,
        publication_year=work.publication_year,
        venue=work.venue,
        doi=work.doi,
        arxiv_id=work.arxiv_id,
        semantic_scholar_id=work.semantic_scholar_id,
        language=work.language,
        state=StateOut(
            importance=state.importance,
            familiarity=state.familiarity,
            reading_status=state.reading_status,
            notes=state.notes,
        ),
        documents=[
            DocumentOut(
                id=document.id,
                original_filename=document.original_filename,
                mime_type=document.mime_type,
                page_count=document.page_count,
                parse_status=document.parse_status,
                parse_error=document.parse_error,
            )
            for document in work.documents
        ],
        section_count=counts[0],
        embedded_section_count=counts[1],
        figure_count=figure_count or 0,
        created_at=work.created_at,
        updated_at=work.updated_at,
    )


def fact_to_out(fact: models.StructuredFact) -> StructuredFactOut:
    return StructuredFactOut(
        id=fact.id,
        fact_type=fact.fact_type,
        value=fact.value,
        confidence=fact.confidence,
        evidence_text=fact.evidence_text,
        extractor=fact.extractor,
        review_status=fact.review_status,
        created_at=fact.created_at,
    )


def analysis_to_out(session: Session, work: models.Work, warnings: list[str] | None = None) -> WorkAnalysisOut:
    facts = list(
        session.scalars(
            select(models.StructuredFact)
            .where(models.StructuredFact.work_id == work.id)
            .order_by(models.StructuredFact.created_at.desc(), models.StructuredFact.fact_type.asc())
        )
    )
    return WorkAnalysisOut(
        work_id=work.id,
        work_title=work.title,
        facts=[fact_to_out(fact) for fact in facts],
        warnings=warnings or [],
    )
