from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.database import Base


EMBEDDING_DIMENSION = get_settings().embedding_dimension


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Work(TimestampMixin, Base):
    __tablename__ = "works"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('paper', 'book', 'thesis', 'report', 'chapter')",
            name="ck_works_kind",
        ),
        CheckConstraint(
            "publication_year IS NULL OR publication_year BETWEEN 1000 AND 3000",
            name="ck_works_publication_year",
        ),
        UniqueConstraint("doi", name="uq_works_doi"),
        UniqueConstraint("arxiv_id", name="uq_works_arxiv_id"),
        UniqueConstraint("semantic_scholar_id", name="uq_works_semantic_scholar_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[str] = mapped_column(String(24), default="paper", nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text)
    publication_date: Mapped[date | None] = mapped_column(Date)
    publication_year: Mapped[int | None] = mapped_column(Integer)
    venue: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(255))
    arxiv_id: Mapped[str | None] = mapped_column(String(64))
    semantic_scholar_id: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(16))
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=sql_text("'{}'::jsonb"), nullable=False
    )

    authors: Mapped[list[WorkAuthor]] = relationship(
        back_populates="work", cascade="all, delete-orphan", order_by="WorkAuthor.position"
    )
    tags: Mapped[list[WorkTag]] = relationship(
        back_populates="work", cascade="all, delete-orphan"
    )
    state: Mapped[UserWorkState | None] = relationship(
        back_populates="work", cascade="all, delete-orphan", uselist=False
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="work", cascade="all, delete-orphan"
    )
    sections: Mapped[list[Section]] = relationship(
        back_populates="work", cascade="all, delete-orphan"
    )
    facts: Mapped[list[StructuredFact]] = relationship(
        back_populates="work", cascade="all, delete-orphan"
    )
    figures: Mapped[list[Figure]] = relationship(
        back_populates="work", cascade="all, delete-orphan"
    )


Index("ix_works_year", Work.publication_year)
Index("ix_works_title_trgm", Work.title, postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"})


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    orcid: Mapped[str | None] = mapped_column(String(32), unique=True)

    works: Mapped[list[WorkAuthor]] = relationship(back_populates="author")


class WorkAuthor(Base):
    __tablename__ = "work_authors"

    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), primary_key=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("authors.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    work: Mapped[Work] = relationship(back_populates="authors")
    author: Mapped[Author] = relationship(back_populates="works")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))

    works: Mapped[list[WorkTag]] = relationship(back_populates="tag")


class WorkTag(Base):
    __tablename__ = "work_tags"
    __table_args__ = (
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_work_tags_confidence"),
    )

    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(32), default="human", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    work: Mapped[Work] = relationship(back_populates="tags")
    tag: Mapped[Tag] = relationship(back_populates="works")


class UserWorkState(TimestampMixin, Base):
    __tablename__ = "user_work_states"
    __table_args__ = (
        CheckConstraint("importance BETWEEN 0 AND 5", name="ck_state_importance"),
        CheckConstraint("familiarity BETWEEN 0 AND 5", name="ck_state_familiarity"),
        CheckConstraint(
            "reading_status IN ('unread', 'queued', 'reading', 'read', 'archived')",
            name="ck_state_reading_status",
        ),
    )

    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), primary_key=True
    )
    importance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    familiarity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reading_status: Mapped[str] = mapped_column(String(24), default="unread", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    work: Mapped[Work] = relationship(back_populates="state")


class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_kind: Mapped[str] = mapped_column(String(32), default="upload", nullable=False)
    original_filename: Mapped[str | None] = mapped_column(Text)
    relative_path: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64), unique=True)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    license: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    parse_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    parse_error: Mapped[str | None] = mapped_column(Text)

    work: Mapped[Work] = relationship(back_populates="documents")
    sections: Mapped[list[Section]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    figures: Mapped[list[Figure]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Section(TimestampMixin, Base):
    __tablename__ = "sections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(32), default="chunk", nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        server_default=sql_text("'{}'::jsonb"),
        nullable=False,
    )
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('simple', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('simple', coalesce(text, '')), 'B')",
            persisted=True,
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSION))
    embedding_model: Mapped[str | None] = mapped_column(Text)

    work: Mapped[Work] = relationship(back_populates="sections")
    document: Mapped[Document | None] = relationship(back_populates="sections")
    parent: Mapped[Section | None] = relationship(remote_side="Section.id")


Index("ix_sections_search_vector", Section.search_vector, postgresql_using="gin")
Index(
    "ix_sections_embedding_hnsw",
    Section.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
    postgresql_where=Section.embedding.is_not(None),
)


class CitationEdge(TimestampMixin, Base):
    __tablename__ = "citation_edges"

    citing_work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), primary_key=True
    )
    cited_work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    context: Mapped[str | None] = mapped_column(Text)


class Figure(TimestampMixin, Base):
    __tablename__ = "figures"
    __table_args__ = (
        CheckConstraint("page_number >= 1", name="ck_figures_page_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    context_text: Mapped[str | None] = mapped_column(Text)
    image_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    bounding_box: Mapped[list[float] | None] = mapped_column(JSONB)
    extraction_method: Mapped[str] = mapped_column(
        String(64), default="embedded_image", nullable=False
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSION))
    embedding_model: Mapped[str | None] = mapped_column(Text)

    work: Mapped[Work] = relationship(back_populates="figures")
    document: Mapped[Document] = relationship(back_populates="figures")


Index(
    "ix_figures_embedding_hnsw",
    Figure.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
    postgresql_where=Figure.embedding.is_not(None),
)
Index("ix_figures_page", Figure.document_id, Figure.page_number)


class StructuredFact(TimestampMixin, Base):
    __tablename__ = "structured_facts"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_structured_facts_confidence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sections.id", ondelete="SET NULL")
    )
    evidence_text: Mapped[str | None] = mapped_column(Text)
    extractor: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), default="unreviewed", nullable=False)

    work: Mapped[Work] = relationship(back_populates="facts")
