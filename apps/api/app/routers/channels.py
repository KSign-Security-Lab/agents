"""Channels: shared, streamed, branchable.

A channel is a named, shared space — one continuous message feed plus the
document set the agent answers from. There's no folder-then-session layering
above it; a channel just is the conversation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from api.app.agent import citations as cit
from api.app.agent.graph import run_agent
from api.app.agent.resolve import resolve_citations
from api.app.db.models import (
    AgentRun,
    AgentStep,
    Channel,
    ChannelDocument,
    Chunk,
    Citation,
    Document,
    Message,
    MessageRole,
    MessageStatus,
)
from api.app.db.session import SessionLocal
from api.app.deps import CurrentUser, DbSession
from api.app.schemas import (
    BranchOut,
    ChannelCreate,
    ChannelOut,
    ChannelUpdate,
    CitationOut,
    MessageOut,
    RevertRequest,
    ScopeUpdate,
    SendMessage,
    UserOut,
)
from api.app.services import messages as tree, retrieval
from api.app.services.locks import run_lock
from api.app.services.realtime import realtime

log = logging.getLogger("routers.channels")
router = APIRouter(prefix="/channels", tags=["channels"])


def _user_out(u) -> UserOut | None:
    return UserOut(id=u.id, email=u.email, name=u.name, role=u.role.value) if u else None


# ------------------------------------------------------------------ CRUD -----
@router.post("", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(body: ChannelCreate, db: DbSession, user: CurrentUser) -> ChannelOut:
    if await db.scalar(select(Channel.id).where(Channel.name == body.name.strip())):
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 사용 중인 채널 이름입니다")

    channel = Channel(name=body.name.strip(), description=body.description,
                      created_by=user.id)
    db.add(channel)
    await db.flush()

    for doc_id in dict.fromkeys(body.document_ids):
        if await db.get(Document, doc_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"문서를 찾을 수 없습니다: {doc_id}")
        db.add(ChannelDocument(channel_id=channel.id, document_id=doc_id, added_by=user.id))
    await db.flush()
    await db.refresh(channel, ["creator"])
    out = await _channel_out(db, channel)

    await realtime.publish_channel_list("channel.created", {
        "id": str(channel.id), "name": channel.name, "by": user.name,
    })
    return out


@router.get("", response_model=list[ChannelOut])
async def list_channels(db: DbSession, user: CurrentUser) -> list[ChannelOut]:
    """Every channel in the workspace — channels are shared, not private."""
    channels = (await db.execute(
        select(Channel).options(selectinload(Channel.creator))
        .where(Channel.archived.is_(False)).order_by(Channel.updated_at.desc())
    )).scalars().unique().all()
    return [await _channel_out(db, c) for c in channels]


@router.get("/{channel_id}", response_model=ChannelOut)
async def get_channel(channel_id: UUID, db: DbSession, user: CurrentUser) -> ChannelOut:
    return await _channel_out(db, await _require(db, channel_id))


@router.patch("/{channel_id}", response_model=ChannelOut)
async def update_channel(channel_id: UUID, body: ChannelUpdate, db: DbSession,
                         user: CurrentUser) -> ChannelOut:
    channel = await _require(db, channel_id)
    if body.name is not None:
        name = body.name.strip()
        existing = await db.scalar(
            select(Channel.id).where(Channel.name == name, Channel.id != channel_id)
        )
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "이미 사용 중인 채널 이름입니다")
        channel.name = name
    if body.description is not None:
        channel.description = body.description
    await db.flush()
    return await _channel_out(db, channel)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_channel(channel_id: UUID, db: DbSession, user: CurrentUser) -> None:
    """Archive rather than delete: a channel owns its messages, so hard-deleting
    it would destroy a transcript others were relying on."""
    channel = await _require(db, channel_id)
    channel.archived = True
    await db.flush()
    await realtime.publish_channel_list("channel.archived", {
        "id": str(channel_id), "by": user.name,
    })


# ------------------------------------------------------------------ scope -----
@router.get("/{channel_id}/documents", response_model=list[UUID])
async def channel_documents(channel_id: UUID, db: DbSession, user: CurrentUser) -> list[UUID]:
    await _require(db, channel_id)
    return await retrieval.channel_document_ids(db, channel_id)


@router.patch("/{channel_id}/documents", response_model=list[UUID])
async def update_documents(channel_id: UUID, body: ScopeUpdate, db: DbSession,
                           user: CurrentUser) -> list[UUID]:
    """Add or remove documents from the channel's set — flat, no delta layer."""
    await _require(db, channel_id)
    for doc_id in body.add:
        if await db.get(Document, doc_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"문서를 찾을 수 없습니다: {doc_id}")
        exists = await db.scalar(
            select(ChannelDocument.document_id).where(
                ChannelDocument.channel_id == channel_id,
                ChannelDocument.document_id == doc_id,
            )
        )
        if not exists:
            db.add(ChannelDocument(channel_id=channel_id, document_id=doc_id, added_by=user.id))
    if body.remove:
        await db.execute(delete(ChannelDocument).where(
            ChannelDocument.channel_id == channel_id,
            ChannelDocument.document_id.in_(body.remove),
        ))
    await db.flush()
    return await retrieval.channel_document_ids(db, channel_id)


# --------------------------------------------------------------- messages -----
@router.get("/{channel_id}/messages", response_model=list[MessageOut])
async def list_messages(channel_id: UUID, db: DbSession, user: CurrentUser) -> list[MessageOut]:
    """The live branch, with sibling counts so the UI can offer branch switching."""
    channel = await _require(db, channel_id)
    path = await tree.active_path(db, channel)
    out: list[MessageOut] = []
    for m in path:
        idx, count = await tree.sibling_info(db, m)
        out.append(await _message_out(db, m, idx, count))
    return out


@router.get("/{channel_id}/messages/{message_id}/branches", response_model=list[BranchOut])
async def list_branches(channel_id: UUID, message_id: UUID, db: DbSession,
                        user: CurrentUser) -> list[BranchOut]:
    """Alternatives that share this message's parent — the other paths taken."""
    channel = await _require(db, channel_id)
    msg = await db.get(Message, message_id)
    if msg is None or msg.channel_id != channel_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "메시지를 찾을 수 없습니다")

    siblings = (await db.execute(
        select(Message).where(Message.channel_id == channel_id,
                              Message.parent_id == msg.parent_id,
                              Message.role == msg.role)
        .order_by(Message.created_at)
    )).scalars().all()

    active = {m.id for m in await tree.active_path(db, channel)}
    return [
        BranchOut(message_id=s.id, preview=(s.content or "")[:120],
                  created_at=s.created_at, is_active=s.id in active)
        for s in siblings
    ]


@router.post("/{channel_id}/revert", response_model=list[MessageOut])
async def revert(channel_id: UUID, body: RevertRequest, db: DbSession,
                 user: CurrentUser) -> list[MessageOut]:
    """Move the live path back to a checkpoint.

    This only moves ``active_leaf_id``: everything after the checkpoint stays in
    the database and remains reachable through the branch switcher. Continuing
    from here forks a new branch instead of overwriting what was there.
    """
    channel = await _require(db, channel_id)
    target = await db.get(Message, body.message_id)
    if target is None or target.channel_id != channel_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "메시지를 찾을 수 없습니다")

    dropped = await tree.descendants_count(db, target.id)
    channel.active_leaf_id = target.id
    await db.flush()

    await realtime.publish_channel(str(channel_id), "branch.reverted", {
        "channel_id": str(channel_id), "message_id": str(target.id),
        "by": user.name, "messages_after": dropped,
    })
    return await list_messages(channel_id, db, user)


@router.post("/{channel_id}/switch/{message_id}", response_model=list[MessageOut])
async def switch_branch(channel_id: UUID, message_id: UUID, db: DbSession,
                        user: CurrentUser) -> list[MessageOut]:
    """Make another branch the live one, following it down to its leaf."""
    channel = await _require(db, channel_id)
    msg = await db.get(Message, message_id)
    if msg is None or msg.channel_id != channel_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "메시지를 찾을 수 없습니다")

    channel.active_leaf_id = await tree.leaf_of(db, message_id)
    await db.flush()
    await realtime.publish_channel(str(channel_id), "branch.switched", {
        "channel_id": str(channel_id), "message_id": str(message_id), "by": user.name,
    })
    return await list_messages(channel_id, db, user)


@router.post("/{channel_id}/messages")
async def send_message(channel_id: UUID, body: SendMessage, request: Request,
                       db: DbSession, user: CurrentUser):
    """Post a question and stream the answer.

    Everything is streamed twice: once down this response to the caller, and once
    onto the channel's Redis channel so every other viewer sees the same tokens
    arrive. That is what makes a shared channel feel like one conversation rather
    than several private ones.
    """
    channel = await _require(db, channel_id)

    parent_id = body.parent_id or channel.active_leaf_id
    branch_root = await tree.branch_root_for(db, parent_id)

    user_msg = Message(
        channel_id=channel_id, parent_id=parent_id, branch_root_id=branch_root,
        role=MessageRole.user, author_id=user.id, content=body.content.strip(),
        status=MessageStatus.complete,
    )
    db.add(user_msg)
    await db.flush()

    assistant = Message(
        channel_id=channel_id, parent_id=user_msg.id,
        branch_root_id=branch_root or user_msg.id,
        role=MessageRole.assistant, content="", status=MessageStatus.queued,
    )
    db.add(assistant)
    await db.flush()
    channel.active_leaf_id = assistant.id

    history = await tree.history_for_model(db, channel)
    document_ids = await retrieval.channel_document_ids(db, channel_id)
    await db.commit()

    await realtime.publish_channel(str(channel_id), "message", {
        "id": str(user_msg.id), "parent_id": str(parent_id) if parent_id else None,
        "role": "user", "author": {"id": str(user.id), "name": user.name},
        "content": user_msg.content, "status": "complete",
        "created_at": user_msg.created_at.isoformat(),
    })

    return EventSourceResponse(
        _stream_turn(request, channel_id, assistant.id, user_msg.content,
                     history, document_ids),
        ping=15000,
    )


async def _stream_turn(request: Request, channel_id: UUID, assistant_id: UUID,
                       question: str, history: list[dict], document_ids: list[UUID]):
    """Run the agent, persist the result, and fan every event out to viewers."""
    lock_token = str(uuid.uuid4())
    channel_send = realtime.publish_channel

    async with SessionLocal() as db:
        got_lock = await run_lock.acquire(str(channel_id), lock_token)
        if not got_lock:
            depth = await run_lock.queue_depth(str(channel_id))
            payload = {"message": "이 채널에서 다른 답변이 생성 중입니다. 잠시 후 다시 시도해 주세요.",
                       "queued_ahead": depth}
            yield {"event": "error", "data": json.dumps(payload, ensure_ascii=False)}
            return

        answer = ""
        citations: list[cit.Citation] = []
        sources_by_sid: dict[int, dict] = {}
        steps: list[dict] = []
        rejected: list[int] = []
        run_id: str | None = None
        error: str | None = None

        try:
            msg = await db.get(Message, assistant_id)
            msg.status = MessageStatus.running
            await db.commit()

            async for ev in run_agent(db, question=question, history=history,
                                      document_ids=document_ids):
                if await request.is_disconnected():
                    # The author closed the tab; other viewers may still be
                    # watching, so the run continues and only this stream stops.
                    log.info("caller disconnected from channel %s", channel_id)

                data = dict(ev.data)
                if ev.type == "token":
                    answer += data["text"]
                elif ev.type == "citation":
                    citations.append(_rehydrate(data))
                elif ev.type == "revision":
                    answer = data.get("text", answer)
                    for c in data.get("citations", []):
                        citations.append(_rehydrate(c))
                elif ev.type == "step":
                    steps.append(data)
                elif ev.type == "done":
                    run_id = data.get("run_id")
                    rejected = data.get("rejected") or []
                    for s in data.get("sources", []):
                        sources_by_sid[s["sid"]] = s
                elif ev.type == "error":
                    error = data.get("message")

                payload = json.dumps({"channel_id": str(channel_id),
                                      "message_id": str(assistant_id), **data},
                                     ensure_ascii=False, default=str)
                yield {"event": ev.type, "data": payload}
                await channel_send(str(channel_id), ev.type,
                                   {"message_id": str(assistant_id), **data})

            resolved = await _persist(db, assistant_id, answer, citations,
                                      sources_by_sid, steps, run_id, rejected, error)

            final = {"message_id": str(assistant_id),
                     "citations": [r.as_dict() for r in resolved]}
            yield {"event": "final", "data": json.dumps(final, ensure_ascii=False, default=str)}
            await channel_send(str(channel_id), "final", final)

        except Exception as exc:  # noqa: BLE001
            log.exception("turn failed in channel %s", channel_id)
            msg = await db.get(Message, assistant_id)
            if msg is not None:
                msg.status = MessageStatus.failed
                msg.content = answer
                await db.commit()
            yield {"event": "error", "data": json.dumps({"message": str(exc)},
                                                        ensure_ascii=False)}
        finally:
            await run_lock.release(str(channel_id), lock_token)


def _rehydrate(data: dict) -> cit.Citation:
    """Rebuild a Citation from the streamed event payload."""
    return cit.Citation(
        idx=data["idx"],
        source=cit.SourceRef(
            sid=data["sid"], chunk_id=data.get("chunk_id") or 0,
            document_id=data["document_id"], filename=data.get("filename", ""),
            text="", out_of_scope=bool(data.get("out_of_scope")),
        ),
    )


async def _persist(db, assistant_id: UUID, answer: str, citations: list[cit.Citation],
                   sources_by_sid: dict[int, dict], steps: list[dict],
                   run_id: str | None, rejected: list[int], error: str | None):
    """Store the answer, then resolve citation geometry.

    Geometry is resolved after streaming rather than during it: the sentence
    alignment needs the finished sentence a pill was attached to, which only
    exists once the stream ends.
    """
    msg = await db.get(Message, assistant_id)
    msg.content = answer
    msg.status = MessageStatus.failed if error else MessageStatus.complete
    from api.app.config import settings as cfg

    msg.model = cfg.served_model_name
    await db.flush()

    # Fill in the chunk text the resolver needs to align sentences.
    chunk_ids = [c.source.chunk_id for c in citations if c.source.chunk_id]
    if chunk_ids:
        rows = (await db.execute(
            select(Chunk.id, Chunk.text, Chunk.heading_path, Chunk.page_from,
                   Chunk.page_to, Chunk.t_start_ms, Chunk.t_end_ms)
            .where(Chunk.id.in_(chunk_ids))
        )).all()
        by_id = {r[0]: r for r in rows}
        for c in citations:
            row = by_id.get(c.source.chunk_id)
            if row:
                c.source.text = row[1] or ""
                c.source.heading_path = row[2]
                c.source.page_from, c.source.page_to = row[3], row[4]
                c.source.t_start_ms, c.source.t_end_ms = row[5], row[6]

    resolved = await resolve_citations(db, answer, citations)

    await db.execute(delete(Citation).where(Citation.message_id == assistant_id))
    for r in resolved:
        db.add(Citation(
            message_id=assistant_id, idx=r.idx, document_id=UUID(r.document_id),
            chunk_id=r.chunk_id, page_no=r.page_no,
            rects=[x.as_dict() for x in r.rects],
            t_start_ms=r.t_start_ms, t_end_ms=r.t_end_ms,
            snippet=r.snippet, heading_path=r.heading_path, score=r.score,
            out_of_scope=r.out_of_scope,
        ))

    if run_id:
        run = AgentRun(id=UUID(run_id), message_id=assistant_id,
                       status="failed" if error else "complete",
                       rejected_citations=rejected or None, error=error)
        db.add(run)
        await db.flush()
        for s in steps:
            db.add(AgentStep(run_id=run.id, ord=s.get("ord", 0), node=s.get("node", "?"),
                             label=s.get("label"), output_json=s.get("output")))
    await db.commit()
    return resolved


# ------------------------------------------------------------------ events ----
@router.get("/{channel_id}/events")
async def channel_events(channel_id: UUID, request: Request, db: DbSession,
                         user: CurrentUser):
    """Everything happening in this channel, for every viewer.

    Includes other people's messages and the assistant tokens they triggered, so
    opening a channel someone else is using shows the answer arriving live.
    """
    await _require(db, channel_id)

    async def gen():
        await realtime.heartbeat(str(channel_id), str(user.id), user.name)
        viewers = await realtime.viewers(str(channel_id))
        yield {"event": "presence", "data": json.dumps({"viewers": viewers},
                                                       ensure_ascii=False)}

        beat = asyncio.create_task(_presence_loop(channel_id, user))
        try:
            async for event in realtime.subscribe(realtime.channel_key(str(channel_id))):
                if await request.is_disconnected():
                    break
                yield {"event": event["event"],
                       "data": json.dumps(event["data"], ensure_ascii=False, default=str)}
        finally:
            beat.cancel()

    return EventSourceResponse(gen(), ping=15000)


async def _presence_loop(channel_id: UUID, user) -> None:
    """Refresh this viewer's presence key so "3 viewing" stays accurate."""
    try:
        while True:
            await realtime.heartbeat(str(channel_id), str(user.id), user.name)
            await asyncio.sleep(10)
    except asyncio.CancelledError:
        pass


# ----------------------------------------------------------------- helpers ----
async def _require(db, channel_id: UUID) -> Channel:
    channel = (await db.execute(
        select(Channel).options(selectinload(Channel.creator))
        .where(Channel.id == channel_id)
    )).scalar_one_or_none()
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "채널을 찾을 수 없습니다")
    return channel


async def _channel_out(db, c: Channel) -> ChannelOut:
    msg_count = await db.scalar(select(func.count()).select_from(Message)
                                .where(Message.channel_id == c.id))
    docs = await retrieval.channel_document_ids(db, c.id)
    return ChannelOut(
        id=c.id, name=c.name, description=c.description,
        created_by=_user_out(c.creator), active_leaf_id=c.active_leaf_id,
        message_count=msg_count or 0, document_count=len(docs),
        archived=c.archived, created_at=c.created_at, updated_at=c.updated_at,
    )


async def _message_out(db, m: Message, sibling_index: int, sibling_count: int) -> MessageOut:
    docs = {}
    if m.citations:
        rows = (await db.execute(
            select(Document.id, Document.filename)
            .where(Document.id.in_([c.document_id for c in m.citations]))
        )).all()
        docs = {r[0]: r[1] for r in rows}
    return MessageOut(
        id=m.id, channel_id=m.channel_id, parent_id=m.parent_id, role=m.role.value,
        author=_user_out(m.author), content=m.content, status=m.status.value,
        citations=[
            CitationOut(**{**tree.citation_out(c),
                           "filename": docs.get(c.document_id, "")})
            for c in m.citations
        ],
        created_at=m.created_at, sibling_index=sibling_index, sibling_count=sibling_count,
    )
