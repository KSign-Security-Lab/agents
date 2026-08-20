"""Job enqueue helpers.

The API never runs ingest itself: OCR and ASR take tens of seconds and would
block a request. The worker owns them.
"""
from __future__ import annotations

import logging
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings

from api.app.config import settings

log = logging.getLogger("services.queue")

_pool = None


async def pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def enqueue_ingest(document_id: UUID, *, force: bool = False) -> None:
    p = await pool()
    await p.enqueue_job("ingest", str(document_id), force)
    log.info("queued ingest for %s (force=%s)", document_id, force)


async def enqueue_topic_merge() -> None:
    p = await pool()
    await p.enqueue_job("merge_topics")
