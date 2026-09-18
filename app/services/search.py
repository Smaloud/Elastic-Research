from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings
from app.schemas import SearchHit, SearchMode, SearchResponse
from app.services.embeddings import EmbeddingUnavailable, get_embedder
from app.services.serialization import work_to_out
from app.services.text_processing import make_snippet


@dataclass
class Candidate:
    work: models.Work
    section: models.Section
    raw_score: float


def rrf_scores(
    ranked_lists: Sequence[Sequence[uuid.UUID]], rank_constant: int = 60
) -> dict[uuid.UUID, float]:
    scores: dict[uuid.UUID, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (rank_constant + rank)
    return scores


def _apply_filters(
    statement,
    *,
    year_from: int | None,
    year_to: int | None,
    tag: str | None,
    min_importance: int | None,
    max_familiarity: int | None,
):
    if year_from is not None:
        statement = statement.where(models.Work.publication_year >= year_from)
    if year_to is not None:
        statement = statement.where(models.Work.publication_year <= year_to)
    if min_importance is not None:
        statement = statement.where(
            func.coalesce(models.UserWorkState.importance, 0) >= min_importance
        )
    if max_familiarity is not None:
        statement = statement.where(
            func.coalesce(models.UserWorkState.familiarity, 0) <= max_familiarity
        )
    if tag:
        normalized = tag.strip().casefold().replace("_", "-")
        statement = statement.where(
            exists(
                select(models.WorkTag.work_id)
                .join(models.Tag, models.Tag.id == models.WorkTag.tag_id)
                .where(models.WorkTag.work_id == models.Work.id)
                .where(models.Tag.normalized_name == normalized)
            )
        )
    return statement


def _dedupe_candidates(rows, score_index: int) -> list[Candidate]:
    result: list[Candidate] = []
    seen: set[uuid.UUID] = set()
    for row in rows:
        section, work = row[0], row[1]
        if work.id in seen:
            continue
        seen.add(work.id)
        result.append(Candidate(work=work, section=section, raw_score=float(row[score_index])))
    return result


def _base_statement():
    return (
        select(models.Section, models.Work, models.UserWorkState)
        .join(models.Work, models.Work.id == models.Section.work_id)
        .outerjoin(models.UserWorkState, models.UserWorkState.work_id == models.Work.id)
    )


def _keyword_candidates(session: Session, query: str, limit: int, **filters) -> list[Candidate]:
    tsquery = func.websearch_to_tsquery("simple", query)
    score = func.ts_rank_cd(models.Section.search_vector, tsquery).label("rank_score")
    ranked = (
        _apply_filters(
            select(
                models.Section.id.label("section_id"),
                score,
                func.row_number()
                .over(partition_by=models.Section.work_id, order_by=score.desc())
                .label("work_rank"),
            )
            .join(models.Work, models.Work.id == models.Section.work_id)
            .outerjoin(
                models.UserWorkState,
                models.UserWorkState.work_id == models.Work.id,
            )
            .where(models.Section.search_vector.op("@@")(tsquery)),
            **filters,
        )
        .subquery()
    )
    statement = (
        _base_statement()
        .add_columns(ranked.c.rank_score)
        .join(ranked, ranked.c.section_id == models.Section.id)
        .where(ranked.c.work_rank == 1)
        .order_by(ranked.c.rank_score.desc())
        .limit(limit)
    )
    rows = session.execute(statement).all()
    return _dedupe_candidates(rows, 3)[:limit]


def _semantic_candidates(session: Session, query: str, limit: int, **filters) -> list[Candidate]:
    query_vector = get_embedder().embed_query(query)
    distance = models.Section.embedding.cosine_distance(query_vector).label("distance")
    ranked = (
        _apply_filters(
            select(
                models.Section.id.label("section_id"),
                distance,
                func.row_number()
                .over(partition_by=models.Section.work_id, order_by=distance.asc())
                .label("work_rank"),
            )
            .join(models.Work, models.Work.id == models.Section.work_id)
            .outerjoin(
                models.UserWorkState,
                models.UserWorkState.work_id == models.Work.id,
            )
            .where(models.Section.embedding.is_not(None)),
            **filters,
        )
        .subquery()
    )
    statement = (
        _base_statement()
        .add_columns(ranked.c.distance)
        .join(ranked, ranked.c.section_id == models.Section.id)
        .where(ranked.c.work_rank == 1)
        .order_by(ranked.c.distance.asc())
        .limit(limit)
    )
    rows = session.execute(statement).all()
    candidates = _dedupe_candidates(rows, 3)[:limit]
    for candidate in candidates:
        candidate.raw_score = 1.0 - candidate.raw_score
    return candidates


def _recent_works(session: Session, limit: int, **filters) -> list[Candidate]:
    statement = (
        select(models.Work, models.UserWorkState)
        .outerjoin(models.UserWorkState, models.UserWorkState.work_id == models.Work.id)
    )
    statement = _apply_filters(statement, **filters)
    rows = session.execute(statement.order_by(models.Work.created_at.desc()).limit(limit)).all()
    candidates: list[Candidate] = []
    for work, _ in rows:
        section = session.scalar(
            select(models.Section)
            .where(models.Section.work_id == work.id)
            .order_by(models.Section.ordinal.asc())
            .limit(1)
        )
        if section is not None:
            candidates.append(Candidate(work=work, section=section, raw_score=0.0))
    return candidates


def search_library(
    session: Session,
    *,
    query: str,
    mode: SearchMode,
    limit: int,
    year_from: int | None = None,
    year_to: int | None = None,
    tag: str | None = None,
    min_importance: int | None = None,
    max_familiarity: int | None = None,
) -> SearchResponse:
    query = query.strip()
    filters = {
        "year_from": year_from,
        "year_to": year_to,
        "tag": tag,
        "min_importance": min_importance,
        "max_familiarity": max_familiarity,
    }
    warnings: list[str] = []
    candidate_limit = max(limit, get_settings().search_candidate_limit)

    if not query:
        candidates = _recent_works(session, limit, **filters)
        hits = [
            SearchHit(
                work=work_to_out(session, candidate.work),
                score=0.0,
                matched_section_id=candidate.section.id,
                matched_section_title=candidate.section.title,
                page_start=candidate.section.page_start,
                page_end=candidate.section.page_end,
                snippet=make_snippet(candidate.section.text, ""),
            )
            for candidate in candidates
        ]
        return SearchResponse(
            query="",
            requested_mode=mode,
            mode_used="recent",
            total=len(hits),
            hits=hits,
        )

    lexical: list[Candidate] = []
    semantic: list[Candidate] = []
    if mode in {"keyword", "hybrid"}:
        lexical = _keyword_candidates(session, query, candidate_limit, **filters)
    if mode in {"semantic", "hybrid"}:
        try:
            semantic = _semantic_candidates(session, query, candidate_limit, **filters)
        except EmbeddingUnavailable as exc:
            if mode == "semantic":
                raise
            warnings.append(f"向量检索暂不可用，已降级为关键词检索：{exc}")

    if mode == "keyword" or (mode == "hybrid" and not semantic):
        ranked = lexical
        scores = {candidate.work.id: candidate.raw_score for candidate in ranked}
        mode_used = "keyword"
    elif mode == "semantic":
        ranked = semantic
        scores = {candidate.work.id: candidate.raw_score for candidate in ranked}
        mode_used = "semantic"
    else:
        lexical_ids = [candidate.work.id for candidate in lexical]
        semantic_ids = [candidate.work.id for candidate in semantic]
        scores = rrf_scores([lexical_ids, semantic_ids])
        # Prefer the lexical evidence span when a work appears in both lists;
        # it usually gives the user a more directly explainable snippet.
        by_id = {candidate.work.id: candidate for candidate in semantic}
        by_id.update({candidate.work.id: candidate for candidate in lexical})
        ranked = [by_id[item_id] for item_id in sorted(scores, key=scores.get, reverse=True)]
        mode_used = "hybrid"

    lexical_ranks = {candidate.work.id: rank for rank, candidate in enumerate(lexical, start=1)}
    semantic_ranks = {candidate.work.id: rank for rank, candidate in enumerate(semantic, start=1)}
    hits = [
        SearchHit(
            work=work_to_out(session, candidate.work),
            score=scores[candidate.work.id],
            matched_section_id=candidate.section.id,
            matched_section_title=candidate.section.title,
            page_start=candidate.section.page_start,
            page_end=candidate.section.page_end,
            snippet=make_snippet(candidate.section.text, query),
            lexical_rank=lexical_ranks.get(candidate.work.id),
            semantic_rank=semantic_ranks.get(candidate.work.id),
        )
        for candidate in ranked[:limit]
    ]
    return SearchResponse(
        query=query,
        requested_mode=mode,
        mode_used=mode_used,
        total=len(hits),
        hits=hits,
        warnings=warnings,
    )
