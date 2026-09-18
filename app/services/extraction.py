from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import models
from app.services.identifiers import normalize_tag
from app.services.llm_client import LLMUnavailable, chat
from app.services.llm_config import load_llm_config


SYSTEM_PROMPT = """你是严谨的科研文献结构化抽取器。只能依据给定原文，不能补充常识或猜测。
返回一个合法 JSON 对象，不要 Markdown，不要解释。格式严格为：
{
  "summary": {"text": "不超过300字的摘要", "evidence": "支持摘要的原文短句"},
  "glossary": [{"term": "术语", "definition": "本文语境下定义", "evidence": "原文短句", "page": 1}],
  "resources": [{"name": "工具/代码/模型/硬件", "role": "用途", "evidence": "原文短句", "page": 1}],
  "methods": [{"name": "方法", "description": "如何使用", "evidence": "原文短句", "page": 1}],
  "datasets": [{"name": "数据集", "usage": "用途/规模/划分", "evidence": "原文短句", "page": 1}],
  "findings": [{"claim": "实验结果或主要结论", "metric": "指标（没有则空）", "evidence": "原文短句", "page": 1}],
  "limitations": [{"text": "局限或未解决问题", "evidence": "原文短句", "page": 1}],
  "tags": ["5到20个简短、可复用、具体的中英文标签"]
}
没有依据的字段使用空数组；evidence 必须是输入中能找到的短文本。"""

CATEGORY_FIELDS = {
    "glossary": "glossary",
    "resources": "resource",
    "methods": "method",
    "datasets": "dataset",
    "findings": "finding",
    "limitations": "limitation",
}


def _json_from_reply(reply: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", reply.strip(), flags=re.I)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise LLMUnavailable("模型没有返回可识别的 JSON；请重试或更换模型")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"模型返回的 JSON 无效：{exc.msg}") from exc
    if not isinstance(data, dict):
        raise LLMUnavailable("模型返回结果不是 JSON 对象")
    return data


def _source_text(session: Session, work: models.Work, max_chars: int) -> tuple[str, bool]:
    sections = list(
        session.scalars(
            select(models.Section)
            .where(models.Section.work_id == work.id)
            .order_by(models.Section.ordinal.asc())
        )
    )
    pieces = [
        f"标题：{work.title}",
        f"摘要：{work.abstract}" if work.abstract else "",
    ]
    used = sum(len(item) for item in pieces)
    truncated = False
    for section in sections:
        marker = f"\n[第{section.page_start}页]" if section.page_start else "\n[元数据]"
        chunk = f"{marker}\n{section.text}"
        if used + len(chunk) > max_chars:
            remaining = max_chars - used
            if remaining > 200:
                pieces.append(chunk[:remaining])
            truncated = True
            break
        pieces.append(chunk)
        used += len(chunk)
    return "\n".join(piece for piece in pieces if piece), truncated


def extract_work(session: Session, work: models.Work) -> list[str]:
    config = load_llm_config()
    source, truncated = _source_text(session, work, config.max_input_chars)
    reply = chat(
        config,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"请抽取以下文献：\n\n{source}"},
        ],
    )
    data = _json_from_reply(reply)
    extractor = f"llm:{config.provider}:{config.model}"

    session.execute(
        delete(models.StructuredFact).where(
            models.StructuredFact.work_id == work.id,
            models.StructuredFact.review_status == "unreviewed",
            models.StructuredFact.extractor.like("llm:%"),
        )
    )
    summary = data.get("summary")
    if isinstance(summary, dict) and summary.get("text"):
        session.add(
            models.StructuredFact(
                work=work,
                fact_type="summary",
                value={"text": str(summary.get("text", ""))},
                evidence_text=str(summary.get("evidence", "")) or None,
                extractor=extractor,
            )
        )

    for source_key, fact_type in CATEGORY_FIELDS.items():
        items = data.get(source_key, [])
        if not isinstance(items, list):
            continue
        for item in items[:50]:
            if not isinstance(item, dict):
                continue
            evidence = str(item.get("evidence", "")).strip() or None
            value = {key: value for key, value in item.items() if key != "evidence"}
            if value:
                session.add(
                    models.StructuredFact(
                        work=work,
                        fact_type=fact_type,
                        value=value,
                        evidence_text=evidence,
                        extractor=extractor,
                    )
                )

    tags = data.get("tags", [])
    if isinstance(tags, list):
        seen: set[str] = set()
        for raw in tags[:20]:
            name = str(raw).strip()[:120]
            normalized = normalize_tag(name)
            if name and normalized and normalized not in seen:
                seen.add(normalized)
                session.add(
                    models.StructuredFact(
                        work=work,
                        fact_type="suggested_tag",
                        value={"name": name},
                        confidence=0.7,
                        extractor=extractor,
                    )
                )
    session.commit()
    warnings = []
    if truncated:
        warnings.append(
            f"原文超过 {config.max_input_chars} 字符，本次只分析了前半部分；长文分段汇总将在下一阶段加入。"
        )
    return warnings


def apply_fact_review(session: Session, fact: models.StructuredFact, status: str) -> None:
    fact.review_status = status
    if fact.fact_type == "suggested_tag" and status == "accepted":
        name = str(fact.value.get("name", "")).strip()
        normalized = normalize_tag(name)
        if name and normalized:
            tag = session.scalar(select(models.Tag).where(models.Tag.normalized_name == normalized))
            if tag is None:
                tag = models.Tag(name=name, normalized_name=normalized, category="llm")
                session.add(tag)
                session.flush()
            exists = session.scalar(
                select(models.WorkTag).where(
                    models.WorkTag.work_id == fact.work_id,
                    models.WorkTag.tag_id == tag.id,
                )
            )
            if exists is None:
                session.add(
                    models.WorkTag(
                        work_id=fact.work_id,
                        tag_id=tag.id,
                        source="llm-reviewed",
                        confidence=fact.confidence or 0.7,
                    )
                )
    session.commit()
