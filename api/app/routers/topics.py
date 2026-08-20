"""Topic taxonomy management.

The taxonomy is emergent, so its maintenance surface matters: the agent will
occasionally propose two names for one idea, and an admin needs to be able to see
and fix that without editing anything by hand.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from api.app.db.models import DocumentTopic, Topic, TopicAlias
from api.app.deps import AdminUser, CurrentUser, DbSession
from api.app.ingest import categorize
from api.app.schemas import MergeCandidate, TopicOut, TopicRename

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("", response_model=list[TopicOut])
async def list_topics(db: DbSession, user: CurrentUser) -> list[TopicOut]:
    counts = dict((await db.execute(
        select(DocumentTopic.topic_id, func.count()).group_by(DocumentTopic.topic_id)
    )).all())
    topics = (await db.execute(
        select(Topic).where(Topic.merged_into_id.is_(None)).order_by(Topic.name)
    )).scalars().all()
    rows = [TopicOut(id=t.id, name=t.name, slug=t.slug, doc_count=counts.get(t.id, 0))
            for t in topics]
    rows.sort(key=lambda r: (-r.doc_count, r.name))
    return rows


@router.get("/{topic_id}/aliases", response_model=list[str])
async def topic_aliases(topic_id: UUID, db: DbSession, user: CurrentUser) -> list[str]:
    """Names that were folded into this topic — the audit trail for a merge."""
    return list((await db.execute(
        select(TopicAlias.alias).where(TopicAlias.topic_id == topic_id)
        .order_by(TopicAlias.alias)
    )).scalars().all())


@router.get("/merge-candidates", response_model=list[MergeCandidate])
async def merge_candidates(db: DbSession, user: CurrentUser) -> list[MergeCandidate]:
    """Pairs similar enough to be worth reviewing but not identical.

    These are deliberately not merged automatically: at this similarity range
    "기술제안서" and "기술보고서" are close in embedding space and different in
    meaning, so a person decides.
    """
    return [MergeCandidate(**c) for c in await categorize.merge_candidates(db)]


@router.patch("/{topic_id}", response_model=TopicOut)
async def rename_topic(topic_id: UUID, body: TopicRename, db: DbSession,
                       admin: AdminUser) -> TopicOut:
    topic = await db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "토픽을 찾을 수 없습니다")

    # The old name becomes an alias so the agent's earlier labelling stays traceable.
    if topic.name != body.name:
        db.add(TopicAlias(topic_id=topic.id, alias=topic.name))
    topic.name = body.name.strip()
    topic.slug = categorize.slugify(topic.name)
    topic.created_by = "user"
    await db.flush()
    count = await db.scalar(select(func.count()).select_from(DocumentTopic)
                            .where(DocumentTopic.topic_id == topic_id))
    return TopicOut(id=topic.id, name=topic.name, slug=topic.slug, doc_count=count or 0)


@router.post("/merge", status_code=status.HTTP_204_NO_CONTENT)
async def merge(keep_id: UUID, drop_id: UUID, db: DbSession, admin: AdminUser) -> None:
    try:
        await categorize.merge_topics(db, keep_id, drop_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.post("/auto-merge")
async def auto_merge(db: DbSession, admin: AdminUser) -> dict:
    """Fold away near-identical topics created by concurrent ingests."""
    merged = await categorize.merge_similar_topics(db)
    return {"merged": merged}
