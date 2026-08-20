"""Emergent topic taxonomy.

The agent proposes Korean topic names freely; near-duplicates are then folded
together by embedding similarity rather than matched against a fixed list. That
is what lets the taxonomy grow with the corpus while still converging — "계약서"
and "계약문서" become one topic, and the surface form the agent used is kept as an
alias so the merge stays auditable.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text as sql
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.agent.prompts import ko
from api.app.config import settings
from api.app.db.models import Document, DocumentTopic, Topic, TopicAlias
from api.app.services import llm_client
from api.app.services.infer_client import infer_client

log = logging.getLogger("ingest.categorize")

# How much of a document the classifier reads. Front matter plus a sample is
# enough to name the topic, and it keeps the call cheap on a 300-page contract.
HEAD_CHARS = 6000
SAMPLE_CHARS = 2000


@dataclass(slots=True)
class Categorization:
    topics: list[str]
    summary: str
    key_entities: list[str]
    suggested_questions: list[str]


def slugify(name: str) -> str:
    """Slug that keeps Korean intact.

    Transliterating or stripping Hangul would collapse distinct topics onto the
    same slug, so only whitespace and punctuation are normalised.
    """
    s = unicodedata.normalize("NFC", name).strip().lower()
    s = re.sub(r"[\s/]+", "-", s)
    s = re.sub(r"[^0-9a-z가-힣ㄱ-ㆎ\-]+", "", s)
    return re.sub(r"-{2,}", "-", s).strip("-") or "topic"


def build_document_view(chunk_texts: list[str]) -> str:
    """The text handed to the classifier: the opening, then a spread of samples.

    Reading only the head would misclassify a document whose subject appears
    later; reading everything would be wasteful and often exceed the context.
    """
    if not chunk_texts:
        return ""
    head = "\n\n".join(chunk_texts)[:HEAD_CHARS]
    if len(chunk_texts) <= 4:
        return head

    step = max(1, len(chunk_texts) // 4)
    sampled = "\n\n".join(chunk_texts[i] for i in range(step, len(chunk_texts), step))
    return f"{head}\n\n[...]\n\n{sampled[:SAMPLE_CHARS]}"


async def classify(chunk_texts: list[str], *, filename: str) -> Categorization:
    view = build_document_view(chunk_texts)
    if not view.strip():
        return Categorization(topics=[], summary="", key_entities=[], suggested_questions=[])

    messages = [
        {"role": "system", "content": ko.CATEGORIZE_SYSTEM},
        {"role": "user", "content": f"[파일명]\n{filename}\n\n[문서 내용]\n{view}"},
    ]
    raw = await llm_client.complete_json(messages, ko.CATEGORIZE_SCHEMA, max_tokens=900,
                                         name="classify_document")
    return Categorization(
        topics=[t.strip() for t in raw.get("topics", []) if t and t.strip()][:4],
        summary=(raw.get("summary") or "").strip(),
        key_entities=[e.strip() for e in raw.get("key_entities", []) if e and e.strip()][:10],
        suggested_questions=[q.strip() for q in raw.get("suggested_questions", [])
                             if q and q.strip()][:3],
    )


async def resolve_topics(db: AsyncSession, names: list[str]) -> list[Topic]:
    """Map proposed names onto topics, reusing an existing one when close enough.

    Matching is done on embeddings, not strings: "계약/법무" and "법무·계약" are the
    same topic to a reader and to a vector, but share no substring.
    """
    if not names:
        return []

    out = await infer_client.embed(names, kind="query", with_sparse=False)
    vectors = out["dense"]
    resolved: list[Topic] = []

    for name, vec in zip(names, vectors):
        existing = await _nearest_topic(db, vec)
        if existing is not None:
            topic, distance = existing
            similarity = 1.0 - distance
            if similarity >= settings.topic_merge_threshold:
                log.info("topic %r folded into %r (similarity %.3f)",
                         name, topic.name, similarity)
                await _add_alias(db, topic, name)
                resolved.append(topic)
                continue

        slug = slugify(name)
        clash = (await db.execute(select(Topic).where(Topic.slug == slug))).scalar_one_or_none()
        if clash is not None:
            # Same slug, different meaning: keep both but make the slug unique.
            slug = f"{slug}-{len(resolved) + 1}"

        topic = Topic(name=name, slug=slug, embedding=vec, created_by="agent")
        db.add(topic)
        await db.flush()
        resolved.append(topic)

    # De-duplicate within one document's proposals.
    seen: set[UUID] = set()
    unique: list[Topic] = []
    for t in resolved:
        if t.id in seen:
            continue
        seen.add(t.id)
        unique.append(t)
    return unique


async def _nearest_topic(db: AsyncSession, vector: list[float]) -> tuple[Topic, float] | None:
    row = (await db.execute(sql("""
        SELECT id, embedding <=> CAST(:v AS vector) AS distance
        FROM topics
        WHERE embedding IS NOT NULL AND merged_into_id IS NULL
        ORDER BY embedding <=> CAST(:v AS vector)
        LIMIT 1
    """), {"v": str(vector)})).fetchone()
    if row is None:
        return None
    topic = await db.get(Topic, row[0])
    return (topic, float(row[1])) if topic else None


async def _add_alias(db: AsyncSession, topic: Topic, alias: str) -> None:
    if alias.strip() == topic.name.strip():
        return
    exists = (await db.execute(
        select(TopicAlias).where(TopicAlias.topic_id == topic.id, TopicAlias.alias == alias)
    )).scalar_one_or_none()
    if exists is None:
        db.add(TopicAlias(topic_id=topic.id, alias=alias))


async def apply(db: AsyncSession, document: Document, cat: Categorization) -> list[Topic]:
    """Persist the classification and attach the document to its topics."""
    document.summary = cat.summary or None
    document.key_entities = cat.key_entities or None
    document.suggested_questions = cat.suggested_questions or None

    topics = await resolve_topics(db, cat.topics)
    for t in topics:
        exists = (await db.execute(
            select(DocumentTopic).where(DocumentTopic.document_id == document.id,
                                        DocumentTopic.topic_id == t.id)
        )).scalar_one_or_none()
        if exists is None:
            db.add(DocumentTopic(document_id=document.id, topic_id=t.id, source="agent"))
            t.doc_count = (t.doc_count or 0) + 1
    await db.flush()
    return topics


async def merge_similar_topics(db: AsyncSession, *, threshold: float | None = None) -> int:
    """Sweep the taxonomy and fold near-duplicate topics together.

    Per-document resolution only compares against what existed at the time, so
    two documents ingested concurrently can each create a near-duplicate. This
    runs periodically to converge them. The larger topic absorbs the smaller so
    the surviving name is the one more documents already use.
    """
    threshold = threshold or settings.topic_merge_threshold
    topics = (await db.execute(
        select(Topic).where(Topic.merged_into_id.is_(None), Topic.embedding.isnot(None))
        .order_by(Topic.doc_count.desc())
    )).scalars().all()

    merged = 0
    for i, keep in enumerate(topics):
        if keep.merged_into_id is not None:
            continue
        for drop in topics[i + 1:]:
            if drop.merged_into_id is not None:
                continue
            similarity = 1.0 - _cosine_distance(keep.embedding, drop.embedding)
            if similarity < threshold:
                continue
            log.info("merging topic %r into %r (similarity %.3f)",
                     drop.name, keep.name, similarity)
            await db.execute(sql("""
                INSERT INTO document_topics (document_id, topic_id, confidence, source)
                SELECT document_id, :keep, confidence, source
                FROM document_topics WHERE topic_id = :drop
                ON CONFLICT (document_id, topic_id) DO NOTHING
            """), {"keep": str(keep.id), "drop": str(drop.id)})
            await db.execute(sql("DELETE FROM document_topics WHERE topic_id = :drop"),
                             {"drop": str(drop.id)})
            await _add_alias(db, keep, drop.name)
            drop.merged_into_id = keep.id
            merged += 1

    if merged:
        await _recount(db)
    await db.flush()
    return merged


async def merge_candidates(db: AsyncSession, *, low: float | None = None,
                           high: float | None = None) -> list[dict]:
    """Topic pairs similar enough to be worth a human look, but not identical.

    Auto-merging everything in this band would eventually collapse genuinely
    distinct topics ("기술제안서" and "기술보고서" are close in embedding space and
    different in meaning), so the decision is surfaced rather than taken.
    """
    low = low if low is not None else settings.topic_suggest_threshold
    high = high if high is not None else settings.topic_merge_threshold

    topics = (await db.execute(
        select(Topic).where(Topic.merged_into_id.is_(None), Topic.embedding.isnot(None))
    )).scalars().all()

    out: list[dict] = []
    for i, a in enumerate(topics):
        for b in topics[i + 1:]:
            similarity = 1.0 - _cosine_distance(a.embedding, b.embedding)
            if low <= similarity < high:
                keep, drop = (a, b) if (a.doc_count or 0) >= (b.doc_count or 0) else (b, a)
                out.append({
                    "similarity": round(similarity, 4),
                    "suggested_keep": {"id": str(keep.id), "name": keep.name,
                                       "doc_count": keep.doc_count},
                    "suggested_drop": {"id": str(drop.id), "name": drop.name,
                                       "doc_count": drop.doc_count},
                })
    out.sort(key=lambda r: r["similarity"], reverse=True)
    return out


async def merge_topics(db: AsyncSession, keep_id: UUID, drop_id: UUID) -> None:
    """Fold one topic into another by explicit instruction (admin action)."""
    keep = await db.get(Topic, keep_id)
    drop = await db.get(Topic, drop_id)
    if keep is None or drop is None or keep.id == drop.id:
        raise ValueError("both topics must exist and differ")

    await db.execute(sql("""
        INSERT INTO document_topics (document_id, topic_id, confidence, source)
        SELECT document_id, :keep, confidence, 'user'
        FROM document_topics WHERE topic_id = :drop
        ON CONFLICT (document_id, topic_id) DO NOTHING
    """), {"keep": str(keep.id), "drop": str(drop.id)})
    await db.execute(sql("DELETE FROM document_topics WHERE topic_id = :drop"),
                     {"drop": str(drop.id)})
    await _add_alias(db, keep, drop.name)
    drop.merged_into_id = keep.id
    await _recount(db)


async def _recount(db: AsyncSession) -> None:
    await db.execute(sql("""
        UPDATE topics t
        SET doc_count = COALESCE(c.n, 0)
        FROM (SELECT topic_id, COUNT(*) n FROM document_topics GROUP BY topic_id) c
        WHERE t.id = c.topic_id
    """))
    await db.execute(sql("""
        UPDATE topics SET doc_count = 0
        WHERE id NOT IN (SELECT topic_id FROM document_topics)
    """))
    await db.flush()


def _cosine_distance(a, b) -> float:
    import math

    if a is None or b is None:
        return 1.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 1.0
    return 1.0 - dot / math.sqrt(na * nb)
