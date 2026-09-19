from __future__ import annotations

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from app import models
from app.schemas import ResearchFactOut, ResearchFactResults
from app.services.identifiers import normalize_tag


def query_facts(
    session: Session,
    *,
    query: str = "",
    fact_types: list[str] | None = None,
    tag: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    min_confidence: float | None = None,
    limit: int = 100,
) -> ResearchFactResults:
    statement = (
        select(models.StructuredFact, models.Work)
        .join(models.Work, models.Work.id == models.StructuredFact.work_id)
        .where(models.StructuredFact.review_status == "accepted")
    )
    if fact_types:
        statement = statement.where(models.StructuredFact.fact_type.in_(fact_types))
    if year_from:
        statement = statement.where(models.Work.publication_year >= year_from)
    if year_to:
        statement = statement.where(models.Work.publication_year <= year_to)
    if min_confidence is not None:
        statement = statement.where(models.StructuredFact.confidence >= min_confidence)
    if tag and (normalized := normalize_tag(tag)):
        tagged_work_ids = (
            select(models.WorkTag.work_id)
            .join(models.Tag, models.Tag.id == models.WorkTag.tag_id)
            .where(models.Tag.normalized_name == normalized)
        )
        statement = statement.where(models.StructuredFact.work_id.in_(tagged_work_ids))
    if query.strip():
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            or_(
                models.Work.title.ilike(pattern),
                models.StructuredFact.evidence_text.ilike(pattern),
                cast(models.StructuredFact.value, Text).ilike(pattern),
            )
        )

    filtered = statement.subquery()
    total = session.scalar(select(func.count()).select_from(filtered)) or 0
    rows = session.execute(
        statement.order_by(
            models.Work.publication_year.desc().nullslast(),
            models.Work.title.asc(),
            models.StructuredFact.fact_type.asc(),
        ).limit(limit)
    ).all()
    return ResearchFactResults(
        total=total,
        facts=[
            ResearchFactOut(
                id=fact.id,
                work_id=work.id,
                work_title=work.title,
                publication_year=work.publication_year,
                fact_type=fact.fact_type,
                value=fact.value,
                confidence=fact.confidence,
                evidence_text=fact.evidence_text,
            )
            for fact, work in rows
        ],
    )
