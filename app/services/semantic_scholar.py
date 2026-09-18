from __future__ import annotations

import threading
import time
import uuid
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import models
from app.schemas import S2PaperCandidate, WorkCreate
from app.services.identifiers import normalize_arxiv_id, normalize_doi
from app.services.library import create_work
from app.services.s2_config import load_s2_config


BASE_URL = "https://api.semanticscholar.org/graph/v1"
PAPER_FIELDS = (
    "paperId,corpusId,title,abstract,authors,year,publicationDate,venue,"
    "citationCount,referenceCount,externalIds,url,openAccessPdf"
)
THROTTLE_SECONDS = 1.05
_request_lock = threading.Lock()
_last_request_at = 0.0


class SemanticScholarUnavailable(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    config = load_s2_config()
    headers = {"User-Agent": "Paperlib/0.2 (personal research library)"}
    if config.api_key:
        headers["x-api-key"] = config.api_key
    return headers


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    global _last_request_at
    with _request_lock:
        remaining = THROTTLE_SECONDS - (time.monotonic() - _last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        try:
            with httpx.Client(timeout=45, follow_redirects=True) as client:
                response = client.get(
                    f"{BASE_URL}{path}", headers=_headers(), params=params
                )
            _last_request_at = time.monotonic()
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise SemanticScholarUnavailable("Semantic Scholar 返回了非预期数据")
            return data
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                detail = "请求过于频繁；请稍后重试，或在设置中添加 Semantic Scholar API Key"
            elif status == 404:
                detail = "没有找到对应论文"
            elif status in {401, 403}:
                detail = "API Key 无效或没有访问权限"
            else:
                detail = exc.response.text[:500]
            raise SemanticScholarUnavailable(
                f"Semantic Scholar 返回 {status}：{detail}"
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise SemanticScholarUnavailable(f"无法连接 Semantic Scholar：{exc}") from exc


def _candidate(raw: dict[str, Any]) -> S2PaperCandidate | None:
    paper_id = raw.get("paperId")
    title = raw.get("title")
    if not paper_id or not title:
        return None
    external = raw.get("externalIds") or {}
    open_pdf = raw.get("openAccessPdf") or {}
    publication_date = None
    if raw.get("publicationDate"):
        try:
            publication_date = date.fromisoformat(raw["publicationDate"])
        except ValueError:
            pass
    return S2PaperCandidate(
        paper_id=str(paper_id),
        corpus_id=raw.get("corpusId"),
        title=str(title),
        abstract=raw.get("abstract"),
        authors=[item.get("name", "") for item in raw.get("authors") or [] if item.get("name")],
        year=raw.get("year"),
        publication_date=publication_date,
        venue=raw.get("venue") or None,
        citation_count=raw.get("citationCount"),
        reference_count=raw.get("referenceCount"),
        doi=normalize_doi(external.get("DOI")),
        arxiv_id=normalize_arxiv_id(external.get("ArXiv")),
        url=raw.get("url"),
        open_access_pdf_url=open_pdf.get("url"),
    )


def mark_existing(session: Session, papers: list[S2PaperCandidate]) -> None:
    if not papers:
        return
    paper_ids = [paper.paper_id for paper in papers]
    dois = [paper.doi for paper in papers if paper.doi]
    arxiv_ids = [paper.arxiv_id for paper in papers if paper.arxiv_id]
    clauses = [models.Work.semantic_scholar_id.in_(paper_ids)]
    if dois:
        clauses.append(models.Work.doi.in_(dois))
    if arxiv_ids:
        clauses.append(models.Work.arxiv_id.in_(arxiv_ids))
    existing = list(session.scalars(select(models.Work).where(or_(*clauses))))
    existing_s2 = {item.semantic_scholar_id for item in existing if item.semantic_scholar_id}
    existing_dois = {item.doi for item in existing if item.doi}
    existing_arxiv = {item.arxiv_id for item in existing if item.arxiv_id}
    for paper in papers:
        paper.already_in_library = (
            paper.paper_id in existing_s2
            or bool(paper.doi and paper.doi in existing_dois)
            or bool(paper.arxiv_id and paper.arxiv_id in existing_arxiv)
        )


def search_papers(session: Session, query: str, limit: int = 20) -> list[S2PaperCandidate]:
    data = _get(
        "/paper/search",
        {"query": query, "limit": limit, "fields": PAPER_FIELDS},
    )
    papers = [paper for raw in data.get("data", []) if (paper := _candidate(raw))]
    mark_existing(session, papers)
    return papers


def get_paper(paper_id: str) -> S2PaperCandidate:
    safe_id = quote(paper_id.strip(), safe=":")
    paper = _candidate(_get(f"/paper/{safe_id}", {"fields": PAPER_FIELDS}))
    if paper is None:
        raise SemanticScholarUnavailable("论文元数据不完整，无法导入")
    return paper


def resolve_work_id(work: models.Work) -> str:
    if work.semantic_scholar_id:
        return work.semantic_scholar_id
    if work.doi:
        return f"DOI:{work.doi}"
    if work.arxiv_id:
        return f"ARXIV:{work.arxiv_id}"
    matches = _get(
        "/paper/search",
        {"query": work.title, "limit": 5, "fields": "paperId,title"},
    ).get("data", [])
    normalized_title = " ".join(work.title.casefold().split())
    for match in matches:
        if " ".join(str(match.get("title", "")).casefold().split()) == normalized_title:
            return str(match["paperId"])
    raise SemanticScholarUnavailable(
        f"无法在 Semantic Scholar 精确匹配《{work.title}》；请先为它补充 DOI 或 arXiv ID"
    )


def related_papers(
    session: Session,
    work: models.Work,
    direction: str,
    limit: int,
) -> list[S2PaperCandidate]:
    paper_id = resolve_work_id(work)
    safe_id = quote(paper_id, safe=":")
    data = _get(
        f"/paper/{safe_id}/{direction}",
        {"limit": limit, "fields": PAPER_FIELDS},
    )
    nested_key = "citingPaper" if direction == "citations" else "citedPaper"
    papers = [
        paper
        for item in data.get("data", [])
        if (paper := _candidate(item.get(nested_key) or {}))
    ]
    for paper in papers:
        paper.matched_seed_ids = [work.id]
        paper.matched_seed_titles = [work.title]
        paper.match_count = 1
    mark_existing(session, papers)
    return papers


def intersect_citations(
    session: Session,
    works: list[models.Work],
    limit_per_seed: int,
    year_from: int | None,
) -> list[S2PaperCandidate]:
    candidates: dict[str, S2PaperCandidate] = {}
    hits: dict[str, set[uuid.UUID]] = {}
    titles = {work.id: work.title for work in works}
    for work in works:
        for paper in related_papers(session, work, "citations", limit_per_seed):
            if year_from and (paper.year is None or paper.year < year_from):
                continue
            candidates[paper.paper_id] = paper
            hits.setdefault(paper.paper_id, set()).add(work.id)

    required = len(works)
    results: list[S2PaperCandidate] = []
    for paper_id, matched_ids in hits.items():
        if len(matched_ids) != required:
            continue
        paper = candidates[paper_id]
        ordered_ids = [work.id for work in works if work.id in matched_ids]
        paper.matched_seed_ids = ordered_ids
        paper.matched_seed_titles = [titles[item] for item in ordered_ids]
        paper.match_count = len(ordered_ids)
        results.append(paper)
    results.sort(
        key=lambda item: (
            item.year or 0,
            item.citation_count or 0,
            item.title.casefold(),
        ),
        reverse=True,
    )
    mark_existing(session, results)
    return results


def import_paper(
    session: Session,
    paper: S2PaperCandidate,
    seed_works: list[models.Work],
    relation: str,
) -> tuple[models.Work, bool, int, list[str]]:
    clauses = [models.Work.semantic_scholar_id == paper.paper_id]
    if paper.doi:
        clauses.append(models.Work.doi == paper.doi)
    if paper.arxiv_id:
        clauses.append(models.Work.arxiv_id == paper.arxiv_id)
    work = session.scalar(select(models.Work).where(or_(*clauses)).limit(1))
    created = work is None
    warnings: list[str] = []
    if work is None:
        result = create_work(
            session,
            WorkCreate(
                kind="paper",
                title=paper.title,
                abstract=paper.abstract,
                authors=paper.authors,
                publication_date=paper.publication_date,
                publication_year=paper.year,
                venue=paper.venue,
                doi=paper.doi,
                arxiv_id=paper.arxiv_id,
                semantic_scholar_id=paper.paper_id,
                tags=["semantic-scholar-import"],
            ),
        )
        work = result.work
        warnings.extend(result.warnings)
    else:
        if not work.semantic_scholar_id:
            work.semantic_scholar_id = paper.paper_id
        work.source_metadata = {
            **(work.source_metadata or {}),
            "semantic_scholar": {
                "paper_id": paper.paper_id,
                "corpus_id": paper.corpus_id,
                "url": paper.url,
                "open_access_pdf_url": paper.open_access_pdf_url,
                "citation_count": paper.citation_count,
                "reference_count": paper.reference_count,
            },
        }

    work.source_metadata = {
        **(work.source_metadata or {}),
        "semantic_scholar": {
            "paper_id": paper.paper_id,
            "corpus_id": paper.corpus_id,
            "url": paper.url,
            "open_access_pdf_url": paper.open_access_pdf_url,
            "citation_count": paper.citation_count,
            "reference_count": paper.reference_count,
        },
    }

    edges_added = 0
    if relation != "none":
        for seed in seed_works:
            if seed.id == work.id:
                continue
            citing_id, cited_id = (
                (work.id, seed.id)
                if relation == "cites_seeds"
                else (seed.id, work.id)
            )
            existing_edge = session.get(
                models.CitationEdge,
                {"citing_work_id": citing_id, "cited_work_id": cited_id},
            )
            if existing_edge is None:
                session.add(
                    models.CitationEdge(
                        citing_work_id=citing_id,
                        cited_work_id=cited_id,
                        source="semantic_scholar",
                    )
                )
                edges_added += 1
    session.commit()
    session.refresh(work)
    return work, created, edges_added, warnings
