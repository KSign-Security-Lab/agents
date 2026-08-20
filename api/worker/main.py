"""arq worker: runs the ingest pipeline off the queue."""
from __future__ import annotations

import logging
from uuid import UUID

from arq.connections import RedisSettings

from api.app.config import settings
from api.app.db.session import SessionLocal
from api.app.ingest.pipeline import ingest_document

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker")


async def ingest(ctx: dict, document_id: str, force: bool = False) -> dict:
    async with SessionLocal() as db:
        result = await ingest_document(db, UUID(document_id), force=force)
    return {"ok": result.ok, "detail": result.detail}


async def merge_topics(ctx: dict) -> dict:
    """Periodic taxonomy convergence.

    Documents ingested concurrently can each mint a near-duplicate topic, since
    per-document resolution only sees what already existed. This folds them.
    """
    from api.app.ingest.categorize import merge_similar_topics

    async with SessionLocal() as db:
        merged = await merge_similar_topics(db)
        await db.commit()
    if merged:
        log.info("merged %d duplicate topic(s)", merged)
    return {"merged": merged}


class WorkerSettings:
    functions = [ingest, merge_topics]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # OCR and ASR are slow by nature; a short timeout would kill legitimate work.
    job_timeout = 3600
    max_jobs = 2               # CPU-bound OCR; more would just thrash
    keep_result = 3600
    health_check_interval = 60
