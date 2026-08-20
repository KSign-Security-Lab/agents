"""Upload, browse, and serve documents.

Files are served through this API rather than by URL so the workspace's
authentication applies; the viewer fetches the PDF here and draws citation
highlights over it client-side.
"""
from __future__ import annotations

import mimetypes
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from api.app.config import settings
from api.app.db.models import (
    DocStatus,
    Document,
    DocumentElement,
    DocumentPage,
    DocumentTopic,
    ElementKind,
    Topic,
)
from api.app.deps import AdminUser, CurrentUser, DbSession
from api.app.ingest import convert
from api.app.schemas import DocumentDetail, DocumentOut, PageOut, TopicOut, UserOut
from api.app.services.queue import enqueue_ingest
from api.app.services.realtime import realtime
from api.app.services.storage import hash_and_store, storage

router = APIRouter(prefix="/documents", tags=["documents"])

CHUNK = 1024 * 1024


def _user_out(u) -> UserOut | None:
    return UserOut(id=u.id, email=u.email, name=u.name, role=u.role.value) if u else None


def _doc_out(d: Document, topics: list[Topic] | None = None) -> DocumentOut:
    return DocumentOut(
        id=d.id, filename=d.filename, mime=d.mime, size_bytes=d.size_bytes,
        source_kind=d.source_kind.value, status=d.status.value, error=d.error,
        page_count=d.page_count, duration_ms=d.duration_ms, summary=d.summary,
        key_entities=d.key_entities, suggested_questions=d.suggested_questions,
        topics=[TopicOut(id=t.id, name=t.name, slug=t.slug, doc_count=t.doc_count or 0)
                for t in (topics or [])],
        uploader=_user_out(d.uploader), created_at=d.created_at,
        has_pdf=bool(d.key_pdf), has_media=bool(d.key_media),
    )


@router.post("", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
async def upload(db: DbSession, user: CurrentUser,
                 file: Annotated[UploadFile, File()]) -> DocumentOut:
    filename = file.filename or "upload"
    try:
        kind = convert.classify(filename, file.content_type)
    except ValueError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from None

    async def stream():
        while True:
            buf = await file.read(CHUNK)
            if not buf:
                return
            yield buf

    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    sha, key, size = await hash_and_store(storage, "originals", suffix, stream())

    if size > settings.max_upload_mb * 1024 * 1024:
        storage.delete(key)
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"최대 {settings.max_upload_mb}MB까지 업로드할 수 있습니다")

    # Identical bytes are the same document; re-uploading is a no-op that simply
    # surfaces the existing one rather than duplicating the ingest work.
    existing = (await db.execute(select(Document).where(Document.sha256 == sha))).scalar_one_or_none()
    if existing is not None:
        return _doc_out(existing, await _topics_for(db, existing.id))

    doc = Document(
        sha256=sha, filename=filename,
        mime=file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        size_bytes=size, source_kind=kind, status=DocStatus.pending,
        key_original=key, uploader_id=user.id,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc, ["uploader"])

    await enqueue_ingest(doc.id)
    await realtime.publish_document("document.status", {
        "document_id": str(doc.id), "filename": doc.filename,
        "status": doc.status.value, "uploader": user.name,
    })
    return _doc_out(doc)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    db: DbSession, user: CurrentUser,
    topic_id: UUID | None = None,
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
) -> list[DocumentOut]:
    stmt = select(Document).options(selectinload(Document.uploader))
    if topic_id:
        stmt = stmt.join(DocumentTopic).where(DocumentTopic.topic_id == topic_id)
    if status_filter:
        stmt = stmt.where(Document.status == DocStatus(status_filter))
    if q:
        stmt = stmt.where(Document.filename.ilike(f"%{q}%"))
    stmt = stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)

    docs = (await db.execute(stmt)).scalars().unique().all()
    topics = await _topics_for_many(db, [d.id for d in docs])
    return [_doc_out(d, topics.get(d.id, [])) for d in docs]


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: UUID, db: DbSession, user: CurrentUser) -> DocumentDetail:
    doc = await _require(db, document_id)
    pages = (await db.execute(
        select(DocumentPage).where(DocumentPage.document_id == document_id)
        .order_by(DocumentPage.page_no)
    )).scalars().all()
    headings = (await db.execute(
        select(DocumentElement)
        .where(DocumentElement.document_id == document_id,
               DocumentElement.kind == ElementKind.heading)
        .order_by(DocumentElement.reading_order)
    )).scalars().all()

    base = _doc_out(doc, await _topics_for(db, document_id))
    return DocumentDetail(
        **base.model_dump(),
        pages=[PageOut(page_no=p.page_no, width=p.width, height=p.height,
                       rotation=p.rotation, has_text_layer=p.has_text_layer) for p in pages],
        outline=[{"page_no": h.page_no, "level": h.level, "text": h.text, "bbox": h.bbox}
                 for h in headings],
    )


@router.get("/{document_id}/pdf")
async def get_pdf(document_id: UUID, db: DbSession, user: CurrentUser):
    """The rendered PDF the viewer draws highlights on."""
    doc = await _require(db, document_id)
    if not doc.key_pdf:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "이 문서에는 PDF 표현이 없습니다")
    path = storage.path(doc.key_pdf)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "저장된 파일을 찾을 수 없습니다")
    return FileResponse(path, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{doc.id}.pdf"'})


@router.get("/{document_id}/media")
async def get_media(document_id: UUID, db: DbSession, user: CurrentUser):
    """The playable recording a transcript citation seeks into.

    Streamed with byte-range support, because the player must be able to jump
    straight to a cited timestamp without downloading the whole file.
    """
    doc = await _require(db, document_id)
    key = doc.key_media or (doc.key_original if convert.is_media(doc.source_kind) else None)
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "재생할 미디어가 없습니다")
    path = storage.path(key)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "저장된 파일을 찾을 수 없습니다")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@router.get("/{document_id}/original")
async def get_original(document_id: UUID, db: DbSession, user: CurrentUser):
    doc = await _require(db, document_id)
    path = storage.path(doc.key_original)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "저장된 파일을 찾을 수 없습니다")
    return StreamingResponse(
        storage.stream(doc.key_original),
        media_type=doc.mime,
        headers={"Content-Disposition": f'attachment; filename="{doc.filename}"'},
    )


@router.get("/{document_id}/transcript")
async def get_transcript(document_id: UUID, db: DbSession, user: CurrentUser) -> list[dict]:
    """Timestamped segments, so the viewer can show a clickable transcript."""
    await _require(db, document_id)
    rows = (await db.execute(
        select(DocumentElement)
        .where(DocumentElement.document_id == document_id,
               DocumentElement.kind == ElementKind.transcript)
        .order_by(DocumentElement.reading_order)
    )).scalars().all()
    return [
        {"text": r.text, "start_ms": (r.table_json or {}).get("start_ms"),
         "end_ms": (r.table_json or {}).get("end_ms")}
        for r in rows
    ]


@router.post("/{document_id}/reingest", response_model=DocumentOut)
async def reingest(document_id: UUID, db: DbSession, user: CurrentUser) -> DocumentOut:
    """Re-run the pipeline from scratch, e.g. after changing the OCR engine."""
    doc = await _require(db, document_id)
    doc.status = DocStatus.pending
    doc.error = None
    await db.flush()
    await enqueue_ingest(doc.id, force=True)
    return _doc_out(doc, await _topics_for(db, document_id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: UUID, db: DbSession, admin: AdminUser) -> None:
    doc = await _require(db, document_id)
    for key in (doc.key_original, doc.key_pdf, doc.key_media):
        if key:
            storage.delete(key)
    await db.execute(delete(Document).where(Document.id == document_id))
    await realtime.publish_document("document.status",
                                    {"document_id": str(document_id), "status": "deleted"})


# ----------------------------------------------------------------- helpers ---
async def _require(db, document_id: UUID) -> Document:
    doc = (await db.execute(
        select(Document).options(selectinload(Document.uploader))
        .where(Document.id == document_id)
    )).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "문서를 찾을 수 없습니다")
    return doc


async def _topics_for(db, document_id: UUID) -> list[Topic]:
    return list((await db.execute(
        select(Topic).join(DocumentTopic)
        .where(DocumentTopic.document_id == document_id, Topic.merged_into_id.is_(None))
        .order_by(Topic.name)
    )).scalars().all())


async def _topics_for_many(db, ids: list[UUID]) -> dict[UUID, list[Topic]]:
    """One query for the whole list, so a 200-document page is not 200 queries."""
    if not ids:
        return {}
    rows = (await db.execute(
        select(DocumentTopic.document_id, Topic)
        .join(Topic, Topic.id == DocumentTopic.topic_id)
        .where(DocumentTopic.document_id.in_(ids), Topic.merged_into_id.is_(None))
        .order_by(Topic.name)
    )).all()
    out: dict[UUID, list[Topic]] = {}
    for doc_id, topic in rows:
        out.setdefault(doc_id, []).append(topic)
    return out
