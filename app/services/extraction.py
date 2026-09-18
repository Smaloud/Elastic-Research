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


TAXONOMY: dict[str, str] = {
    "research_goal": "研究目标/问题",
    "contribution": "主要贡献",
    "terminology": "术语与定义",
    "task": "研究任务",
    "method": "方法流程",
    "algorithm": "算法",
    "architecture": "模型或系统架构",
    "dataset": "数据集/语料",
    "benchmark": "基准任务或基准套件",
    "model": "使用或产出的模型",
    "software": "软件/框架/库",
    "code_repository": "代码仓库/实现资源",
    "hardware": "硬件设备",
    "compute_environment": "算力与运行环境",
    "baseline": "对比基线",
    "metric": "评测指标",
    "hyperparameter": "超参数",
    "training_setup": "训练配置",
    "experimental_setup": "实验设置",
    "evaluation_protocol": "评测协议",
    "statistical_test": "统计检验",
    "experimental_result": "实验结果",
    "ablation": "消融实验",
    "engineering_trick": "工程技巧/实现细节",
    "limitation": "局限与未解决问题",
    "future_work": "未来工作",
    "ethical_consideration": "伦理、安全与社会影响",
}

CATEGORY_ALIASES = {
    "glossary": "terminology",
    "resource": "software",
    "resources": "software",
    "finding": "experimental_result",
    "result": "experimental_result",
    "data": "dataset",
    "experimental_method": "method",
    "experiment_setup": "experimental_setup",
    "evaluation": "evaluation_protocol",
    "engineering": "engineering_trick",
}

MAX_EXTRACTION_CHUNKS = 8

SYSTEM_PROMPT = f"""你是严谨的科研文献结构化抽取器。只能依据给定原文，禁止补充常识、推测或把相关工作误认为本文方法。

必须从下列 category 中选择，不能自创类别：
{json.dumps(TAXONOMY, ensure_ascii=False, indent=2)}

重要区分规则：
- dataset 是实际数据/语料；benchmark 是任务、排行榜或标准评测套件。
- method 是完整研究流程；algorithm 是可执行的计算步骤；architecture 是组件与连接结构。
- model 是模型实体或权重；software 是框架/库/工具；code_repository 是明确的代码或项目地址。
- hardware 是 GPU/CPU/传感器等设备；compute_environment 是数量、显存、运行时长、云环境等组合配置。
- metric 是如何衡量；experimental_result 是某设置下的具体数值或比较结论。
- hyperparameter 是单个参数和值；training_setup 是优化器、批量、轮数等训练组合；experimental_setup 是更广的实验条件。
- baseline 是被比较的方法或系统；ablation 是移除/替换组件的对照；engineering_trick 是实现层面的非核心但有效技巧。
- 引用的其他工作只有在本文实际使用时才可标为 model/software/method/baseline，不能仅因出现在 related work 就抽取。

返回一个合法 JSON 对象，不要 Markdown，不要解释，格式严格为：
{{
  "summary": {{"text": "本分块的事实摘要，不超过300字", "evidence": "原文短句"}},
  "items": [
    {{
      "category": "上方枚举之一",
      "subtype": "更具体的自由文本子类，例如 training_corpus、optimizer、gpu、accuracy",
      "name": "可检索的实体或条目名称",
      "description": "它在本文中是什么、如何使用或得到什么",
      "attributes": {{"类别专属字段": "结构化值"}},
      "page": 1,
      "evidence": "必须逐字来自输入的短文本",
      "confidence": 0.0
    }}
  ],
  "tags": [
    {{"name": "简短、具体、可复用的主题或技术标签", "evidence": "原文短句", "confidence": 0.0}}
  ]
}}

attributes 应按类别保存可比较字段，例如：
- dataset: size, split, preprocessing, language, license, url, usage
- method/algorithm: role, steps, input, output, objective
- model/architecture: version, parameter_count, components, initialization, role
- software/code_repository: version, url, license, role
- hardware/compute_environment: device, count, memory, runtime, precision
- hyperparameter/training_setup: parameter, value, optimizer, learning_rate, batch_size, epochs, scheduler
- benchmark/evaluation_protocol: task, split, protocol, comparison_scope
- metric: definition, direction, aggregation
- experimental_result/ablation: dataset, metric, value, unit, baseline, delta, setting

每个 item 和 tag 必须有 evidence；没有原文依据就不要输出。输出 5 到 20 个 tag。page 必须使用输入中的页码。confidence 是 0 到 1 的证据置信度。"""


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


def _source_chunks(
    session: Session, work: models.Work, max_chars: int
) -> tuple[list[str], bool]:
    sections = list(
        session.scalars(
            select(models.Section)
            .where(models.Section.work_id == work.id)
            .order_by(models.Section.ordinal.asc())
        )
    )
    header = f"标题：{work.title}"
    if work.abstract:
        header += f"\n摘要：{work.abstract}"
    capacity = max(1000, max_chars - len(header) - 2)
    blocks: list[str] = []
    for section in sections:
        marker = f"[第{section.page_start}页]" if section.page_start else "[元数据]"
        text = section.text.strip()
        body_capacity = max(500, capacity - len(marker) - 1)
        for start in range(0, len(text), body_capacity):
            blocks.append(f"{marker}\n{text[start:start + body_capacity]}")

    chunks: list[str] = []
    current: list[str] = [header]
    used = len(header)
    for block in blocks:
        if used + len(block) + 2 > max_chars and len(current) > 1:
            chunks.append("\n\n".join(current))
            current = [header]
            used = len(header)
        current.append(block)
        used += len(block) + 2
    if len(current) > 1 or not chunks:
        chunks.append("\n\n".join(current))

    truncated = len(chunks) > MAX_EXTRACTION_CHUNKS
    return chunks[:MAX_EXTRACTION_CHUNKS], truncated


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _normalize_category(raw: Any) -> str | None:
    category = str(raw or "").strip().casefold().replace("-", "_").replace(" ", "_")
    category = CATEGORY_ALIASES.get(category, category)
    return category if category in TAXONOMY else None


def _normalize_confidence(raw: Any) -> float:
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.7


def _normalize_page(raw: Any) -> int | None:
    try:
        page = int(raw)
        return page if page >= 1 else None
    except (TypeError, ValueError):
        return None


def _normalize_item(
    raw: Any, source: str
) -> tuple[str, dict[str, Any], str, float] | None:
    if not isinstance(raw, dict):
        return None
    category = _normalize_category(raw.get("category"))
    evidence = str(raw.get("evidence", "")).strip()
    if not category or not evidence:
        return None
    if _normalized_text(evidence) not in _normalized_text(source):
        return None

    name = str(raw.get("name", "")).strip()
    description = str(raw.get("description", "")).strip()
    if not name:
        name = description[:120]
    if not name:
        return None
    attributes = raw.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    value = {
        "category_label": TAXONOMY[category],
        "subtype": str(raw.get("subtype", "")).strip() or None,
        "name": name[:500],
        "description": description[:4000] or None,
        "attributes": attributes,
        "page": _normalize_page(raw.get("page")),
    }
    return category, value, evidence[:2000], _normalize_confidence(raw.get("confidence"))


def _attach_auto_tag(
    session: Session, work: models.Work, name: str, confidence: float = 0.75
) -> None:
    normalized = normalize_tag(name)
    if not normalized:
        return
    tag = session.scalar(select(models.Tag).where(models.Tag.normalized_name == normalized))
    if tag is None:
        tag = models.Tag(name=name, normalized_name=normalized, category="llm")
        session.add(tag)
        session.flush()
    link = session.get(
        models.WorkTag,
        {"work_id": work.id, "tag_id": tag.id},
    )
    if link is None:
        session.add(
            models.WorkTag(
                work_id=work.id,
                tag_id=tag.id,
                source="llm-auto",
                confidence=confidence,
            )
        )


def extract_work(session: Session, work: models.Work) -> list[str]:
    config = load_llm_config()
    chunks, truncated = _source_chunks(session, work, config.max_input_chars)
    extracted: list[tuple[dict[str, Any], str]] = []
    for index, source in enumerate(chunks, start=1):
        reply = chat(
            config,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"这是文献的第 {index}/{len(chunks)} 个分块。"
                        "请只抽取本分块中有直接证据的信息，并按 JSON 输出。\n\n"
                        f"{source}"
                    ),
                },
            ],
            json_mode=True,
        )
        extracted.append((_json_from_reply(reply), source))

    extractor = f"llm:{config.provider}:{config.model}:taxonomy-v2"
    session.execute(
        delete(models.StructuredFact).where(
            models.StructuredFact.work_id == work.id,
            models.StructuredFact.extractor.like("llm:%"),
        )
    )
    session.execute(
        delete(models.WorkTag).where(
            models.WorkTag.work_id == work.id,
            models.WorkTag.source == "llm-auto",
        )
    )

    first_summary: tuple[dict[str, Any], str] | None = None
    normalized_items: list[tuple[str, dict[str, Any], str, float]] = []
    tags: dict[str, tuple[str, str, float]] = {}
    skipped_ungrounded = 0
    seen_items: set[tuple[str, str, int | None]] = set()

    for data, source in extracted:
        summary = data.get("summary")
        if first_summary is None and isinstance(summary, dict) and summary.get("text"):
            first_summary = (summary, source)
        items = data.get("items", [])
        if isinstance(items, list):
            for raw in items[:150]:
                item = _normalize_item(raw, source)
                if item is None:
                    skipped_ungrounded += 1
                    continue
                category, value, evidence, confidence = item
                key = (
                    category,
                    _normalized_text(str(value.get("name", ""))),
                    value.get("page"),
                )
                if key in seen_items:
                    continue
                seen_items.add(key)
                normalized_items.append(item)
        raw_tags = data.get("tags", [])
        if isinstance(raw_tags, list):
            for raw_tag in raw_tags:
                if not isinstance(raw_tag, dict):
                    skipped_ungrounded += 1
                    continue
                name = str(raw_tag.get("name", "")).strip()[:120]
                evidence = str(raw_tag.get("evidence", "")).strip()[:2000]
                normalized = normalize_tag(name)
                if (
                    not name
                    or not normalized
                    or not evidence
                    or _normalized_text(evidence) not in _normalized_text(source)
                ):
                    skipped_ungrounded += 1
                    continue
                tags.setdefault(
                    normalized,
                    (name, evidence, _normalize_confidence(raw_tag.get("confidence"))),
                )

    if first_summary:
        summary, source = first_summary
        evidence = str(summary.get("evidence", "")).strip()
        if evidence and _normalized_text(evidence) in _normalized_text(source):
            session.add(
                models.StructuredFact(
                    work=work,
                    fact_type="summary",
                    value={"text": str(summary.get("text", ""))[:4000]},
                    confidence=0.8,
                    evidence_text=evidence[:2000],
                    extractor=extractor,
                    schema_version="v2",
                    review_status="accepted",
                )
            )
        else:
            skipped_ungrounded += 1

    for category, value, evidence, confidence in normalized_items:
        session.add(
            models.StructuredFact(
                work=work,
                fact_type=category,
                value=value,
                confidence=confidence,
                evidence_text=evidence,
                extractor=extractor,
                schema_version="v2",
                review_status="accepted",
            )
        )

    for name, evidence, confidence in list(tags.values())[:20]:
        _attach_auto_tag(session, work, name, confidence)
        session.add(
            models.StructuredFact(
                work=work,
                fact_type="tag",
                value={"name": name},
                confidence=confidence,
                evidence_text=evidence,
                extractor=extractor,
                schema_version="v2",
                review_status="accepted",
            )
        )

    session.commit()
    warnings: list[str] = []
    if len(chunks) > 1:
        warnings.append(f"已分 {len(chunks)} 个原文块完成提取并合并去重。")
    if truncated:
        warnings.append(
            f"文献超过单次任务上限，本次分析了前 {MAX_EXTRACTION_CHUNKS} 个分块。"
        )
    if skipped_ungrounded:
        warnings.append(f"已丢弃 {skipped_ungrounded} 条类别无效或无法对齐原文证据的结果。")
    return warnings


def adopt_existing_extractions(session: Session) -> int:
    """Adopt legacy pending LLM facts after switching to the automatic workflow."""
    facts = list(
        session.scalars(
            select(models.StructuredFact).where(
                models.StructuredFact.extractor.like("llm:%"),
                models.StructuredFact.review_status == "unreviewed",
            )
        )
    )
    for fact in facts:
        fact.review_status = "accepted"
        if fact.fact_type in {"suggested_tag", "tag"}:
            name = str(fact.value.get("name", "")).strip()
            if name:
                _attach_auto_tag(session, fact.work, name, fact.confidence or 0.75)
    if facts:
        session.commit()
    return len(facts)


def apply_fact_review(session: Session, fact: models.StructuredFact, status: str) -> None:
    """Backward-compatible endpoint for records created before automatic adoption."""
    fact.review_status = status
    if fact.fact_type in {"suggested_tag", "tag"} and status == "accepted":
        name = str(fact.value.get("name", "")).strip()
        if name:
            _attach_auto_tag(session, fact.work, name, fact.confidence or 0.75)
    session.commit()
