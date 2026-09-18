from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


WorkKind = Literal["paper", "book", "thesis", "report", "chapter"]
ReadingStatus = Literal["unread", "queued", "reading", "read", "archived"]
SearchMode = Literal["hybrid", "keyword", "semantic"]
LLMProvider = Literal["ollama", "openai_compatible"]


class StateInput(BaseModel):
    importance: int = Field(default=0, ge=0, le=5)
    familiarity: int = Field(default=0, ge=0, le=5)
    reading_status: ReadingStatus = "unread"
    notes: str | None = None


class StatePatch(BaseModel):
    importance: int | None = Field(default=None, ge=0, le=5)
    familiarity: int | None = Field(default=None, ge=0, le=5)
    reading_status: ReadingStatus | None = None
    notes: str | None = None


class WorkCreate(BaseModel):
    kind: WorkKind = "paper"
    title: str = Field(min_length=1, max_length=2000)
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    publication_date: date | None = None
    publication_year: int | None = Field(default=None, ge=1000, le=3000)
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    semantic_scholar_id: str | None = None
    language: str | None = None
    state: StateInput = Field(default_factory=StateInput)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title cannot be blank")
        return value

    @field_validator("authors", "tags")
    @classmethod
    def clean_lists(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for raw in values:
            value = raw.strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result


class StateOut(BaseModel):
    importance: int
    familiarity: int
    reading_status: str
    notes: str | None


class DocumentOut(BaseModel):
    id: uuid.UUID
    original_filename: str | None
    mime_type: str | None
    page_count: int | None
    parse_status: str
    parse_error: str | None


class WorkOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    abstract: str | None
    authors: list[str]
    tags: list[str]
    publication_date: date | None
    publication_year: int | None
    venue: str | None
    doi: str | None
    arxiv_id: str | None
    semantic_scholar_id: str | None
    language: str | None
    state: StateOut
    documents: list[DocumentOut] = Field(default_factory=list)
    section_count: int = 0
    embedded_section_count: int = 0
    figure_count: int = 0
    created_at: datetime
    updated_at: datetime


class IngestResponse(BaseModel):
    work: WorkOut
    warnings: list[str] = Field(default_factory=list)


class SearchHit(BaseModel):
    work: WorkOut
    score: float
    matched_section_id: uuid.UUID | None = None
    matched_section_title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    snippet: str | None = None
    lexical_rank: int | None = None
    semantic_rank: int | None = None


class SearchResponse(BaseModel):
    query: str
    requested_mode: SearchMode
    mode_used: str
    total: int
    hits: list[SearchHit]
    warnings: list[str] = Field(default_factory=list)


class FigureOut(BaseModel):
    id: uuid.UUID
    work_id: uuid.UUID
    work_title: str
    document_id: uuid.UUID
    page_number: int
    ordinal: int
    caption: str | None
    context_text: str | None
    image_url: str
    width: int | None
    height: int | None
    similarity: float | None = None


class FigureSearchResponse(BaseModel):
    query: str
    total: int
    figures: list[FigureOut]
    note: str = (
        "当前按图注与邻近文本做语义相似检索；视觉内容相似需要后续图像向量。"
    )


class LLMSettingsInput(BaseModel):
    enabled: bool = False
    provider: LLMProvider = "ollama"
    base_url: str = Field(default="http://host.docker.internal:11434/v1", max_length=2000)
    model: str = Field(default="qwen3:8b", min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=4000)
    clear_api_key: bool = False
    max_input_chars: int = Field(default=60000, ge=4000, le=300000)
    temperature: float = Field(default=0.1, ge=0, le=2)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("接口地址必须以 http:// 或 https:// 开头")
        return value

    @field_validator("model")
    @classmethod
    def clean_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("模型名称不能为空")
        return value


class LLMSettingsOut(BaseModel):
    enabled: bool
    provider: LLMProvider
    base_url: str
    model: str
    api_key_configured: bool
    api_key_hint: str | None = None
    max_input_chars: int
    temperature: float


class LLMTestOut(BaseModel):
    ok: bool
    message: str
    model: str
    latency_ms: int


class StructuredFactOut(BaseModel):
    id: uuid.UUID
    fact_type: str
    value: dict[str, Any]
    confidence: float | None
    evidence_text: str | None
    extractor: str | None
    review_status: str
    created_at: datetime


class WorkAnalysisOut(BaseModel):
    work_id: uuid.UUID
    work_title: str
    facts: list[StructuredFactOut]
    warnings: list[str] = Field(default_factory=list)


class FactReviewInput(BaseModel):
    status: Literal["accepted", "rejected", "unreviewed"]
