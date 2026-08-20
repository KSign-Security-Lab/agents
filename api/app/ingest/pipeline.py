"""The ingest pipeline.

Each stage records its own state in ``ingest_jobs``, so a failure resumes from
where it stopped instead of re-running a 30-minute OCR or ASR pass. Progress is
published to Redis as it happens, which is what makes the upload UI show real
per-stage progress to everyone in the shared workspace rather than a spinner.

    convert -> extract | transcribe -> chunk -> embed -> categorize -> ready
"""
from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import settings
from api.app.db.models import (
    Chunk as ChunkRow,
    ChunkSpan,
    DocStatus,
    Document,
    DocumentElement,
    DocumentPage,
    IngestJob,
    SourceKind,
)
from api.app.ingest import categorize, chunk as chunker, convert
from api.app.ingest import extract as extractor
from api.app.services.infer_client import infer_client
from api.app.services.realtime import realtime
from api.app.services.retrieval import to_sparse_vector
from api.app.services.storage import LocalStorage, shard_key, storage

log = logging.getLogger("ingest.pipeline")

STAGES = ("convert", "extract", "chunk", "embed", "categorize")
EMBED_BATCH = 16


@dataclass(slots=True)
class StageResult:
    ok: bool
    detail: str = ""


async def _set_status(db: AsyncSession, doc: Document, status: DocStatus,
                      *, error: str | None = None) -> None:
    doc.status = status
    doc.error = error
    await db.flush()
    await realtime.publish_document("document.status", {
        "document_id": str(doc.id), "filename": doc.filename,
        "status": status.value, "error": error,
    })


async def _stage(db: AsyncSession, doc_id: UUID, name: str) -> IngestJob:
    job = (await db.execute(
        select(IngestJob).where(IngestJob.document_id == doc_id, IngestJob.stage == name)
    )).scalar_one_or_none()
    if job is None:
        job = IngestJob(document_id=doc_id, stage=name, status="pending")
        db.add(job)
        await db.flush()
    return job


async def _begin(db: AsyncSession, job: IngestJob) -> None:
    job.status = "running"
    job.attempts = (job.attempts or 0) + 1
    job.started_at = datetime.now(timezone.utc)
    job.error = None
    await db.flush()


async def _finish(db: AsyncSession, job: IngestJob, *, error: str | None = None) -> None:
    job.status = "failed" if error else "done"
    job.error = error
    job.finished_at = datetime.now(timezone.utc)
    await db.flush()


async def ingest_document(db: AsyncSession, document_id: UUID, *,
                          force: bool = False) -> StageResult:
    """Run every outstanding stage for one document."""
    doc = await db.get(Document, document_id)
    if doc is None:
        return StageResult(False, "document not found")

    t0 = time.perf_counter()
    log.info("ingest %s (%s)", doc.filename, doc.source_kind.value)

    with tempfile.TemporaryDirectory(prefix="ingest-") as td:
        work = Path(td)
        try:
            if convert.is_media(doc.source_kind):
                await _run(db, doc, "convert", _stage_media, work, force=force)
                await _run(db, doc, "extract", _stage_transcribe, work, force=force)
            else:
                await _run(db, doc, "convert", _stage_convert, work, force=force)
                await _run(db, doc, "extract", _stage_extract, work, force=force)
            await _run(db, doc, "chunk", _stage_chunk, work, force=force)
            await _run(db, doc, "embed", _stage_embed, work, force=force)
            await _run(db, doc, "categorize", _stage_categorize, work, force=force)
        except Exception as exc:  # noqa: BLE001 - the failure is recorded, not raised
            log.exception("ingest failed for %s", doc.filename)
            await _set_status(db, doc, DocStatus.failed, error=f"{type(exc).__name__}: {exc}")
            await db.commit()
            return StageResult(False, str(exc))

    await _set_status(db, doc, DocStatus.ready)
    await db.commit()
    elapsed = time.perf_counter() - t0
    log.info("ingest done %s in %.1fs", doc.filename, elapsed)
    return StageResult(True, f"{elapsed:.1f}s")


async def _run(db: AsyncSession, doc: Document, name: str, fn, work: Path, *,
               force: bool) -> None:
    job = await _stage(db, doc.id, name)
    if job.status == "done" and not force:
        log.debug("%s: stage %s already done", doc.filename, name)
        return
    await _begin(db, job)
    try:
        await fn(db, doc, work)
    except Exception as exc:
        await _finish(db, job, error=f"{type(exc).__name__}: {exc}")
        raise
    await _finish(db, job)
    await db.commit()


# ------------------------------------------------------------------ stages ---
async def _stage_convert(db: AsyncSession, doc: Document, work: Path) -> None:
    """Produce the PDF everything downstream cites into."""
    await _set_status(db, doc, DocStatus.converting)

    src = _materialize(doc.key_original, work)
    pdf = convert.to_pdf(src, work / "pdf", doc.source_kind)

    if doc.source_kind == SourceKind.pdf:
        doc.key_pdf = doc.key_original
    else:
        key = shard_key("pdf", doc.sha256, ".pdf")
        LocalStorage().copy_in(key, pdf)
        doc.key_pdf = key
    await db.flush()


async def _stage_media(db: AsyncSession, doc: Document, work: Path) -> None:
    """Make the recording playable in a browser and note its duration.

    A citation into a recording is only verifiable if the reader can actually
    play the moment it points at, so a format the browser cannot decode is
    transcoded here rather than at request time.
    """
    await _set_status(db, doc, DocStatus.converting)

    src = _materialize(doc.key_original, work)
    playable = convert.normalize_media_for_playback(src, work / "media", doc.source_kind)
    key = shard_key("media", doc.sha256, playable.suffix)
    LocalStorage().copy_in(key, playable)
    doc.key_media = key
    doc.duration_ms = convert.probe_duration_ms(src) or None
    await db.flush()


async def _stage_extract(db: AsyncSession, doc: Document, work: Path) -> None:
    """Layout extraction: pages, elements, and the span geometry citations need."""
    await _set_status(db, doc, DocStatus.extracting)

    pdf = _materialize(doc.key_pdf or doc.key_original, work)
    result = extractor.extract(pdf)

    await db.execute(delete(DocumentPage).where(DocumentPage.document_id == doc.id))
    await db.execute(delete(DocumentElement).where(DocumentElement.document_id == doc.id))

    for p in result.pages:
        db.add(DocumentPage(document_id=doc.id, page_no=p.page_no, width=p.width,
                            height=p.height, rotation=p.rotation,
                            has_text_layer=p.has_text_layer))
    doc.page_count = len(result.pages)

    # A PDF whose pages all lack a text layer is a scan; recording that lets the
    # UI explain why highlights on it are coarser.
    if result.scanned and doc.source_kind == SourceKind.pdf:
        doc.source_kind = SourceKind.scanned

    for el in result.elements:
        db.add(DocumentElement(
            document_id=doc.id, page_no=el.page_no, kind=el.kind, level=el.level,
            reading_order=el.reading_order, text=el.text, bbox=el.bbox,
            table_json=el.table_json,
        ))
    await db.flush()
    _stash(doc.id, "elements", result.elements)


async def _stage_transcribe(db: AsyncSession, doc: Document, work: Path) -> None:
    """Transcribe a recording into timestamped segments."""
    await _set_status(db, doc, DocStatus.transcribing)

    result = await infer_client.transcribe(doc.key_media or doc.key_original)
    segments = result.get("segments", [])
    if not segments:
        raise RuntimeError("transcription produced no speech segments")

    doc.duration_ms = result.get("duration_ms") or doc.duration_ms
    doc.page_count = None

    await db.execute(delete(DocumentElement).where(DocumentElement.document_id == doc.id))
    from api.app.db.models import ElementKind

    for i, seg in enumerate(segments):
        db.add(DocumentElement(
            document_id=doc.id, page_no=None, kind=ElementKind.transcript, level=None,
            reading_order=i, text=seg["text"], bbox=None,
            table_json={"start_ms": seg["start_ms"], "end_ms": seg["end_ms"]},
        ))
    await db.flush()
    _stash(doc.id, "segments", segments)


async def _stage_chunk(db: AsyncSession, doc: Document, work: Path) -> None:
    await _set_status(db, doc, DocStatus.chunking)

    if convert.is_media(doc.source_kind):
        segments = _unstash(doc.id, "segments") or await _reload_segments(db, doc)
        chunks = chunker.chunk_transcript(segments)
    else:
        elements = _unstash(doc.id, "elements") or await _reload_elements(db, doc)
        chunks = chunker.chunk_elements(elements)

    if not chunks:
        raise RuntimeError("no chunks produced (document appears to contain no text)")

    await db.execute(delete(ChunkRow).where(ChunkRow.document_id == doc.id))
    element_ids = await _element_id_map(db, doc)

    for c in chunks:
        row = ChunkRow(
            document_id=doc.id, ord=c.ord, text=c.text, heading_path=c.heading_path,
            token_count=c.token_estimate, page_from=c.page_from, page_to=c.page_to,
            t_start_ms=c.t_start_ms, t_end_ms=c.t_end_ms, is_table=c.is_table,
            element_id=element_ids.get(c.element_index) if c.element_index is not None else None,
        )
        db.add(row)
        await db.flush()
        for sp in c.spans:
            db.add(ChunkSpan(
                chunk_id=row.id, text_start=sp.text_start, text_end=sp.text_end,
                page_no=sp.page_no, bbox=sp.bbox,
                t_start_ms=sp.t_start_ms, t_end_ms=sp.t_end_ms,
            ))
    await db.flush()
    log.info("%s: %d chunks, %d spans", doc.filename, len(chunks),
             sum(len(c.spans) for c in chunks))


async def _stage_embed(db: AsyncSession, doc: Document, work: Path) -> None:
    """Embed every chunk: dense for semantics, sparse for lexical matching."""
    await _set_status(db, doc, DocStatus.embedding)

    rows = (await db.execute(
        select(ChunkRow).where(ChunkRow.document_id == doc.id).order_by(ChunkRow.ord)
    )).scalars().all()
    if not rows:
        raise RuntimeError("no chunks to embed")

    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start : start + EMBED_BATCH]
        out = await infer_client.embed([r.text for r in batch], kind="passage",
                                       with_sparse=True)
        dense = out["dense"]
        sparse = out.get("sparse") or [{}] * len(batch)
        for row, vec, sp in zip(batch, dense, sparse):
            row.embedding = vec
            row.sparse = to_sparse_vector(sp, settings.sparse_dim)
        await db.flush()
        await realtime.publish_document("document.progress", {
            "document_id": str(doc.id), "stage": "embed",
            "done": min(start + EMBED_BATCH, len(rows)), "total": len(rows),
        })


async def _stage_categorize(db: AsyncSession, doc: Document, work: Path) -> None:
    """Summarize the document and place it in the emergent taxonomy."""
    await _set_status(db, doc, DocStatus.categorizing)

    texts = list((await db.execute(
        select(ChunkRow.text).where(ChunkRow.document_id == doc.id).order_by(ChunkRow.ord)
    )).scalars().all())

    cat = await categorize.classify(texts, filename=doc.filename)
    topics = await categorize.apply(db, doc, cat)
    await realtime.publish_document("document.categorized", {
        "document_id": str(doc.id),
        "topics": [{"id": str(t.id), "name": t.name} for t in topics],
        "summary": doc.summary,
    })


# ----------------------------------------------------------------- helpers ---
# Stage outputs are passed in-process where possible; falling back to the
# database keeps a resumed run correct without re-doing OCR or ASR.
_STASH: dict[tuple[UUID, str], object] = {}


def _stash(doc_id: UUID, key: str, value: object) -> None:
    _STASH[(doc_id, key)] = value


def _unstash(doc_id: UUID, key: str):
    return _STASH.pop((doc_id, key), None)


def _materialize(key: str, work: Path) -> Path:
    """Copy a stored object into the working directory under its own name."""
    src = storage.path(key)
    dst = work / Path(key).name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        dst.write_bytes(src.read_bytes())
    return dst


async def _reload_elements(db: AsyncSession, doc: Document) -> list[extractor.Element]:
    """Rebuild extractor elements from the database for a resumed run.

    Span detail is not persisted per element (it lives on chunks), so a resumed
    chunk stage falls back to element-level geometry. Highlights are then
    paragraph-level for that document until it is re-extracted, which is why
    ``force=True`` re-runs extraction.
    """
    rows = (await db.execute(
        select(DocumentElement).where(DocumentElement.document_id == doc.id)
        .order_by(DocumentElement.reading_order)
    )).scalars().all()
    return [
        extractor.Element(
            kind=r.kind, page_no=r.page_no or 0, reading_order=r.reading_order,
            text=r.text or "", bbox=r.bbox, level=r.level, table_json=r.table_json,
            spans=[extractor.Span(text_start=0, text_end=len(r.text or ""),
                                  page_no=r.page_no, bbox=r.bbox)] if r.bbox else [],
        )
        for r in rows
    ]


async def _reload_segments(db: AsyncSession, doc: Document) -> list[dict]:
    rows = (await db.execute(
        select(DocumentElement).where(DocumentElement.document_id == doc.id)
        .order_by(DocumentElement.reading_order)
    )).scalars().all()
    out = []
    for r in rows:
        meta = r.table_json or {}
        out.append({"text": r.text or "", "start_ms": meta.get("start_ms", 0),
                    "end_ms": meta.get("end_ms", 0)})
    return out


async def _element_id_map(db: AsyncSession, doc: Document) -> dict[int, int]:
    """reading_order -> element row id, so a table chunk can point at its element."""
    rows = (await db.execute(
        select(DocumentElement.reading_order, DocumentElement.id)
        .where(DocumentElement.document_id == doc.id)
    )).all()
    return {r[0]: r[1] for r in rows}
