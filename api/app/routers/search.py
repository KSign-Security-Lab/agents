"""Direct search, without the agent.

Useful for the document browser's search box and for evaluating retrieval
changes in isolation from answer generation.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from api.app.db.models import DocumentTopic
from api.app.deps import CurrentUser, DbSession
from api.app.schemas import SearchHit, SearchRequest
from api.app.services import retrieval

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=list[SearchHit])
async def search(body: SearchRequest, db: DbSession, user: CurrentUser) -> list[SearchHit]:
    document_ids = body.document_ids
    if body.topic_ids:
        by_topic = list((await db.execute(
            select(DocumentTopic.document_id)
            .where(DocumentTopic.topic_id.in_(body.topic_ids))
        )).scalars().all())
        # Intersect rather than union: two filters should narrow, not widen.
        document_ids = ([d for d in document_ids if d in set(by_topic)]
                        if document_ids else by_topic)

    hits = await retrieval.hybrid_search(db, body.query, document_ids=document_ids,
                                         top_k=body.top_k)
    if body.rerank:
        hits = await retrieval.rerank_hits(body.query, hits, top_k=body.top_k)

    return [
        SearchHit(chunk_id=h.chunk_id, document_id=UUID(h.document_id), filename=h.filename,
                  text=h.text, heading_path=h.heading_path, page_from=h.page_from,
                  t_start_ms=h.t_start_ms, score=h.score, rerank_score=h.rerank_score)
        for h in hits
    ]
