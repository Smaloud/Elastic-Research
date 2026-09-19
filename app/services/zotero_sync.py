from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import models
from app.schemas import StateInput, WorkCreate, ZoteroSyncOut
from app.services.identifiers import normalize_arxiv_id, normalize_doi, normalize_name, normalize_tag
from app.services.library import create_work
from app.services.zotero_config import (
    ZoteroConfig,
    load_zotero_config,
    update_zotero_runtime,
)


BASE_URL = "https://api.zotero.org"
_sync_lock = threading.Lock()
SUPPORTED_TYPES = {
    "journalArticle",
    "conferencePaper",
    "preprint",
    "book",
    "bookSection",
    "thesis",
    "report",
    "document",
}


class ZoteroUnavailable(RuntimeError):
    pass


@dataclass
class SyncStats:
    imported: int = 0
    linked: int = 0
    enriched: int = 0
    pushed: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    library_version: int = 0

    def to_out(self) -> ZoteroSyncOut:
        return ZoteroSyncOut(**self.__dict__)


def _headers(config: ZoteroConfig, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Zotero-API-Version": "3",
        "User-Agent": "Paperlib/0.4 (personal research library)",
    }
    if config.api_key:
        headers["Zotero-API-Key"] = config.api_key
    if extra:
        headers.update(extra)
    return headers


def _request(
    config: ZoteroConfig,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: Any = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[Any, httpx.Headers]:
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            response = client.request(
                method,
                f"{BASE_URL}{path}",
                params=params,
                json=payload,
                headers=_headers(config, extra_headers),
            )
        if response.status_code == 304:
            return None, response.headers
        response.raise_for_status()
        data = response.json() if response.content else None
        return data, response.headers
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            detail = "API Key 无效或已撤销"
        elif status == 403:
            detail = "API Key 没有个人资料库读写权限"
        elif status == 409:
            detail = "Zotero 资料库暂时锁定，请稍后重试"
        elif status == 412:
            detail = "同步期间资料库已更新，请重新同步"
        elif status == 429:
            detail = "请求过于频繁，请稍后重试"
        else:
            detail = exc.response.text[:500]
        raise ZoteroUnavailable(f"Zotero 返回 {status}：{detail}") from exc
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ZoteroUnavailable(f"无法连接 Zotero：{exc}") from exc


def verify_connection(config: ZoteroConfig | None = None) -> ZoteroConfig:
    config = config or load_zotero_config()
    if not config.api_key:
        raise ZoteroUnavailable("请先填写 Zotero API Key")
    data, _ = _request(config, "GET", "/keys/current")
    if not isinstance(data, dict):
        raise ZoteroUnavailable("Zotero 没有返回有效的账号信息")
    access = (data.get("access") or {}).get("user") or {}
    if not access.get("library") or not access.get("write"):
        raise ZoteroUnavailable("API Key 必须同时允许读取和写入个人资料库")
    return update_zotero_runtime(
        config,
        user_id=int(data["userID"]),
        username=str(data.get("username") or ""),
    )


def _ensure_identity(config: ZoteroConfig) -> ZoteroConfig:
    return config if config.user_id and config.api_key else verify_connection(config)


def _library_version(headers: httpx.Headers, fallback: int = 0) -> int:
    try:
        return int(headers.get("Last-Modified-Version", fallback))
    except (TypeError, ValueError):
        return fallback


def _creator_names(data: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for creator in data.get("creators") or []:
        if creator.get("name"):
            name = str(creator["name"]).strip()
        else:
            name = " ".join(
                part for part in (creator.get("firstName"), creator.get("lastName")) if part
            ).strip()
        if name:
            names.append(name)
    return names


def _year(value: Any) -> int | None:
    match = re.search(r"\b(1[0-9]{3}|2[0-9]{3})\b", str(value or ""))
    return int(match.group(1)) if match else None


def _arxiv_id(data: dict[str, Any]) -> str | None:
    match = re.search(
        r"(?:arxiv\s*(?:id)?\s*[:=]\s*|arxiv\.org/(?:abs|pdf)/)?([a-z-]+/[0-9]{7}|[0-9]{4}\.[0-9]{4,5})(?:v[0-9]+)?",
        f"{data.get('archiveID') or ''} {data.get('extra') or ''}",
        flags=re.I,
    )
    return normalize_arxiv_id(match.group(0).strip()) if match else None


def _kind(item_type: str) -> str:
    return {
        "book": "book",
        "bookSection": "chapter",
        "thesis": "thesis",
        "report": "report",
    }.get(item_type, "paper")


def _venue(data: dict[str, Any]) -> str | None:
    for field_name in (
        "publicationTitle",
        "proceedingsTitle",
        "bookTitle",
        "university",
        "institution",
        "publisher",
    ):
        if data.get(field_name):
            return str(data[field_name]).strip()
    return None


def _tags(data: dict[str, Any]) -> list[str]:
    return [
        str(item.get("tag", "")).strip()
        for item in data.get("tags") or []
        if str(item.get("tag", "")).strip()
    ]


def _find_linked_work(session: Session, item_key: str) -> models.Work | None:
    return session.scalar(
        select(models.Work)
        .where(models.Work.source_metadata["zotero"]["item_key"].astext == item_key)
        .limit(1)
    )


def _find_matching_work(
    session: Session,
    title: str,
    year: int | None,
    doi: str | None,
    arxiv_id: str | None,
) -> models.Work | None:
    clauses = []
    if doi:
        clauses.append(models.Work.doi == doi)
    if arxiv_id:
        clauses.append(models.Work.arxiv_id == arxiv_id)
    if clauses and (work := session.scalar(select(models.Work).where(or_(*clauses)).limit(1))):
        return work
    query = select(models.Work).where(func.lower(models.Work.title) == title.casefold())
    if year:
        query = query.where(models.Work.publication_year == year)
    return session.scalar(query.limit(1))


def _attach_missing_authors(session: Session, work: models.Work, names: list[str]) -> bool:
    if work.authors or not names:
        return False
    for position, name in enumerate(names):
        normalized = normalize_name(name)
        author = session.scalar(
            select(models.Author).where(models.Author.normalized_name == normalized)
        )
        if author is None:
            author = models.Author(name=name, normalized_name=normalized)
            session.add(author)
            session.flush()
        work.authors.append(models.WorkAuthor(author=author, position=position))
    return True


def _merge_tags(session: Session, work: models.Work, names: list[str]) -> bool:
    existing = {link.tag.normalized_name for link in work.tags}
    changed = False
    for name in names:
        normalized = normalize_tag(name)
        if not normalized or normalized in existing:
            continue
        tag = session.scalar(select(models.Tag).where(models.Tag.normalized_name == normalized))
        if tag is None:
            tag = models.Tag(name=name, normalized_name=normalized, category="zotero")
            session.add(tag)
            session.flush()
        work.tags.append(models.WorkTag(tag=tag, source="zotero", confidence=1.0))
        existing.add(normalized)
        changed = True
    return changed


def _link_work(work: models.Work, config: ZoteroConfig, raw: dict[str, Any]) -> None:
    data = raw.get("data") or {}
    work.source_metadata = {
        **(work.source_metadata or {}),
        "zotero": {
            "library_id": config.user_id,
            "item_key": str(raw.get("key") or data.get("key")),
            "version": int(raw.get("version") or data.get("version") or 0),
            "item_type": data.get("itemType"),
        },
    }


def _import_item(
    session: Session, config: ZoteroConfig, raw: dict[str, Any], stats: SyncStats
) -> None:
    data = raw.get("data") or {}
    item_type = str(data.get("itemType") or "")
    title = str(data.get("title") or "").strip()
    item_key = str(raw.get("key") or data.get("key") or "")
    if item_type not in SUPPORTED_TYPES or not title or not item_key:
        stats.skipped += 1
        return

    work = _find_linked_work(session, item_key)
    doi = normalize_doi(data.get("DOI"))
    arxiv_id = _arxiv_id(data)
    year = _year(data.get("date"))
    if work is None:
        work = _find_matching_work(session, title, year, doi, arxiv_id)
    created = work is None
    if work is None:
        result = create_work(
            session,
            WorkCreate(
                kind=_kind(item_type),
                title=title,
                abstract=str(data.get("abstractNote") or "").strip() or None,
                authors=_creator_names(data),
                tags=_tags(data),
                publication_year=year,
                venue=_venue(data),
                doi=doi,
                arxiv_id=arxiv_id,
                language=str(data.get("language") or "").strip() or None,
                state=StateInput(reading_status="unread"),
            ),
        )
        work = result.work
        stats.warnings.extend(result.warnings)
        stats.imported += 1
    else:
        changed = False
        values = {
            "abstract": str(data.get("abstractNote") or "").strip() or None,
            "publication_year": year,
            "venue": _venue(data),
            "doi": doi,
            "arxiv_id": arxiv_id,
            "language": str(data.get("language") or "").strip() or None,
        }
        for field_name, value in values.items():
            if value and not getattr(work, field_name):
                setattr(work, field_name, value)
                changed = True
        changed |= _attach_missing_authors(session, work, _creator_names(data))
        changed |= _merge_tags(session, work, _tags(data))
        if changed:
            stats.enriched += 1
        stats.linked += 1

    _link_work(work, config, raw)
    session.commit()
    if created:
        session.refresh(work)


def _fetch_changed_items(config: ZoteroConfig) -> tuple[list[dict[str, Any]], int]:
    assert config.user_id is not None
    if not config.sync_collection_keys:
        raise ZoteroUnavailable("请先在 Zotero 页面选择至少一个需要同步的 collection")
    items_by_key: dict[str, dict[str, Any]] = {}
    version = config.last_library_version
    for collection_key in config.sync_collection_keys:
        start = 0
        while True:
            data, headers = _request(
                config,
                "GET",
                f"/users/{config.user_id}/collections/{collection_key}/items/top",
                params={
                    "format": "json",
                    "limit": 100,
                    "start": start,
                    "since": config.last_library_version,
                    "sort": "dateModified",
                    "direction": "asc",
                },
            )
            version = max(version, _library_version(headers, version))
            page = data if isinstance(data, list) else []
            for item in page:
                if isinstance(item, dict) and item.get("key"):
                    items_by_key[str(item["key"])] = item
            if len(page) < 100:
                break
            start += 100
            if start >= 10000:
                raise ZoteroUnavailable(
                    f"Zotero collection {collection_key} 超过单次 10000 条上限"
                )
    return list(items_by_key.values()), version


def list_collections(config: ZoteroConfig | None = None) -> tuple[ZoteroConfig, list[dict[str, str | None]]]:
    config = _ensure_identity(config or load_zotero_config())
    assert config.user_id is not None
    collections: list[dict[str, str | None]] = []
    start = 0
    while True:
        data, _ = _request(
            config,
            "GET",
            f"/users/{config.user_id}/collections",
            params={"format": "json", "limit": 100, "start": start, "sort": "title"},
        )
        page = data if isinstance(data, list) else []
        for raw in page:
            item = raw.get("data") or {}
            if raw.get("key") and item.get("name"):
                collections.append(
                    {
                        "key": str(raw["key"]),
                        "name": str(item["name"]),
                        "parent_key": str(item["parentCollection"]) if item.get("parentCollection") else None,
                    }
                )
        if len(page) < 100:
            break
        start += 100
    return config, collections


def _ensure_collection(config: ZoteroConfig) -> ZoteroConfig:
    assert config.user_id is not None
    if config.collection_key:
        return config
    config, collections = list_collections(config)
    for item in collections:
        if item["name"] == config.collection_name:
            return update_zotero_runtime(config, collection_key=str(item["key"]))

    result, headers = _request(
        config,
        "POST",
        f"/users/{config.user_id}/collections",
        payload=[
            {
                "name": config.collection_name,
                "parentCollection": False,
                "relations": {},
            }
        ],
        extra_headers={"Zotero-Write-Token": uuid.uuid4().hex},
    )
    successful = (result or {}).get("successful") or (result or {}).get("success") or {}
    created = successful.get("0") or successful.get(0)
    key = created.get("key") if isinstance(created, dict) else created
    if not key:
        raise ZoteroUnavailable(f"无法创建 Zotero collection：{(result or {}).get('failed') or result}")
    return update_zotero_runtime(
        config,
        collection_key=str(key),
        last_library_version=_library_version(headers, config.last_library_version),
    )


def _zotero_item(work: models.Work, collection_key: str) -> dict[str, Any]:
    item_type = {
        "book": "book",
        "chapter": "bookSection",
        "thesis": "thesis",
        "report": "report",
    }.get(work.kind, "journalArticle")
    creators = []
    for link in sorted(work.authors, key=lambda item: item.position):
        creators.append({"creatorType": "author", "name": link.author.name})
    metadata = work.source_metadata or {}
    s2 = metadata.get("semantic_scholar") or {}
    tags = [
        {"tag": link.tag.name}
        for link in work.tags
        if link.tag.name != "semantic-scholar-import"
    ]
    if not any(item["tag"].casefold() == "paperlib" for item in tags):
        tags.append({"tag": "Paperlib"})
    extra = [f"Paperlib ID: {work.id}"]
    if work.arxiv_id:
        extra.append(f"arXiv: {work.arxiv_id}")
    if work.doi and item_type != "journalArticle":
        extra.append(f"DOI: {work.doi}")
    payload: dict[str, Any] = {
        "itemType": item_type,
        "title": work.title,
        "creators": creators,
        "abstractNote": work.abstract or "",
        "date": str(work.publication_year or ""),
        "url": str(s2.get("url") or ""),
        "language": work.language or "",
        "extra": "\n".join(extra),
        "tags": tags,
        "collections": [collection_key],
        "relations": {},
    }
    if item_type == "journalArticle":
        payload["publicationTitle"] = work.venue or ""
        payload["DOI"] = work.doi or ""
    elif item_type == "bookSection":
        payload["bookTitle"] = work.venue or ""
    elif item_type == "thesis":
        payload["university"] = work.venue or ""
    elif item_type == "report":
        payload["institution"] = work.venue or ""
    return payload


def _unlinked_works(session: Session, only_work: models.Work | None = None) -> list[models.Work]:
    works = [only_work] if only_work else list(
        session.scalars(select(models.Work).order_by(models.Work.created_at.asc()))
    )
    return [work for work in works if not (work.source_metadata or {}).get("zotero")]


def _push_works(
    session: Session,
    config: ZoteroConfig,
    stats: SyncStats,
    only_work: models.Work | None = None,
) -> ZoteroConfig:
    works = _unlinked_works(session, only_work)
    if not works:
        return config
    config = _ensure_collection(config)
    assert config.user_id is not None and config.collection_key is not None
    for offset in range(0, len(works), 50):
        batch = works[offset : offset + 50]
        payload = [_zotero_item(work, config.collection_key) for work in batch]
        result, headers = _request(
            config,
            "POST",
            f"/users/{config.user_id}/items",
            payload=payload,
            extra_headers={"Zotero-Write-Token": uuid.uuid4().hex},
        )
        successful = (result or {}).get("successful") or (result or {}).get("success") or {}
        failed = (result or {}).get("failed") or {}
        for index, work in enumerate(batch):
            created = successful.get(str(index)) or successful.get(index)
            key = created.get("key") if isinstance(created, dict) else created
            version = created.get("version", 0) if isinstance(created, dict) else 0
            if key:
                _link_work(
                    work,
                    config,
                    {
                        "key": key,
                        "version": version,
                        "data": {"itemType": payload[index]["itemType"]},
                    },
                )
                stats.pushed += 1
            else:
                stats.skipped += 1
                stats.warnings.append(
                    f"《{work.title}》未能写入 Zotero：{failed.get(str(index)) or failed.get(index) or '未知错误'}"
                )
        session.commit()
        config = update_zotero_runtime(
            config,
            last_library_version=_library_version(headers, config.last_library_version),
        )
    return config


def sync_library(session: Session) -> ZoteroSyncOut:
    if not _sync_lock.acquire(blocking=False):
        raise ZoteroUnavailable("Zotero 同步正在进行，请稍后再试")
    try:
        return _sync_library_locked(session)
    finally:
        _sync_lock.release()


def _sync_library_locked(session: Session) -> ZoteroSyncOut:
    config = load_zotero_config()
    if not config.enabled:
        raise ZoteroUnavailable("Zotero 同步尚未启用")
    config = _ensure_identity(config)
    stats = SyncStats(library_version=config.last_library_version)
    items, remote_version = _fetch_changed_items(config)
    for raw in items:
        _import_item(session, config, raw, stats)
    config = update_zotero_runtime(config, last_library_version=remote_version)
    config = _push_works(session, config, stats)
    stats.library_version = config.last_library_version
    return stats.to_out()


def push_work_if_enabled(session: Session, work: models.Work) -> list[str]:
    config = load_zotero_config()
    if not config.enabled or not config.auto_push_discovered:
        return []
    if not _sync_lock.acquire(blocking=False):
        return ["论文已保存；Zotero 正在执行另一轮同步，将在下一轮自动写入。"]
    try:
        config = _ensure_identity(config)
        stats = SyncStats(library_version=config.last_library_version)
        config = _push_works(session, config, stats, only_work=work)
        return stats.warnings
    except ZoteroUnavailable as exc:
        return [f"本地导入成功，但自动写入 Zotero 失败：{exc}"]
    finally:
        _sync_lock.release()
