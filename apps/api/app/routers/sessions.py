"""Chat sessions: shared, streamed, branchable."""
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
    Chunk,
    Citation,
    Document,
    Folder,
    FolderDocument,
    Message,
    MessageRole,
    MessageStatus,
    ScopeMode,
    Session as ChatSession,
    SessionDocument,
)
from api.app.db.session import SessionLocal
from api.app.deps import CurrentUser, DbSession
from api.app.schemas import (
    BranchOut,
    CitationOut,
    MessageOut,
    RevertRequest,
    ScopeUpdate,
    SendMessage,
    SessionCreate,
    SessionOut,
    UserOut,
)
from api.app.services import llm_client, messages as tree, retrieval
from api.app.services.locks import run_lock
from api.app.services.realtime import realtime

log = logging.getLogger("routers.sessions")
router = APIRouter(prefix="/sessions", tags=["sessions"])


def _user_out(u) -> UserOut | None:
    return UserOut(id=u.id, email=u.email, name=u.name, role=u.role.value) if u else None


# ------------------------------------------------------------------ CRUD -----
@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, db: DbSession, user: CurrentUser) -> SessionOut:
    if body.folder_id and await db.get(Folder, body.folder_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "폴더를 찾을 수 없습니다")

    session = ChatSession(title=(body.title or "새 대화").strip(),
                          folder_id=body.folder_id, created_by=user.id)
    db.add(session)
    await db.flush()

    for doc_id in dict.fromkeys(body.document_ids):
        if await db.get(Document, doc_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"문서를 찾을 수 없습니다: {doc_id}")
        db.add(SessionDocument(session_id=session.id, document_id=doc_id,
                               mode=ScopeMode.add, added_by=user.id))
    await db.flush()
    await db.refresh(session, ["creator"])
    return await _session_out(db, session)


@router.get("", response_model=list[SessionOut])
async def list_sessions(db: DbSession, user: CurrentUser,
                        folder_id: UUID | None = None) -> list[SessionOut]:
    """Every session in the workspace — sessions are shared, not private."""
    stmt = select(ChatSession).options(selectinload(ChatSession.creator))
    if folder_id:
        stmt = stmt.where(ChatSession.folder_id == folder_id)
    stmt = stmt.where(ChatSession.archived.is_(False)).order_by(ChatSession.updated_at.desc())
    sessions = (await db.execute(stmt)).scalars().unique().all()
    return [await _session_out(db, s) for s in sessions]


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: UUID, db: DbSession, user: CurrentUser) -> SessionOut:
    return await _session_out(db, await _require(db, session_id))


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_session(session_id: UUID, db: DbSession, user: CurrentUser) -> None:
    """Archive rather than delete: in a shared workspace one person should not be
    able to destroy a transcript others were relying on."""
    session = await _require(db, session_id)
    session.archived = True


# ------------------------------------------------------------------ scope -----
@router.get("/{session_id}/documents", response_model=list[UUID])
async def session_documents(session_id: UUID, db: DbSession, user: CurrentUser) -> list[UUID]:
    await _require(db, session_id)
    return await retrieval.effective_document_ids(db, session_id)


@router.patch("/{session_id}/documents", response_model=list[UUID])
async def update_scope(session_id: UUID, body: ScopeUpdate, db: DbSession,
                       user: CurrentUser) -> list[UUID]:
    """Add or remove individual documents on top of the folder's set.

    Removals are stored explicitly rather than by rewriting the folder, so the
    folder stays reusable and this session's narrowing is local to it.
    """
    await _require(db, session_id)
    for doc_id in body.add:
        await _set_scope(db, session_id, doc_id, ScopeMode.add, user.id)
    for doc_id in body.remove:
        await _set_scope(db, session_id, doc_id, ScopeMode.remove, user.id)
    await db.flush()
    return await retrieval.effective_document_ids(db, session_id)


async def _set_scope(db, session_id: UUID, doc_id: UUID, mode: ScopeMode,
                     user_id: UUID) -> None:
    if await db.get(Document, doc_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"문서를 찾을 수 없습니다: {doc_id}")
    row = (await db.execute(
        select(SessionDocument).where(SessionDocument.session_id == session_id,
                                      SessionDocument.document_id == doc_id)
    )).scalar_one_or_none()
    if row is None:
        db.add(SessionDocument(session_id=session_id, document_id=doc_id,
                               mode=mode, added_by=user_id))
    else:
        row.mode = mode


# --------------------------------------------------------------- messages -----
@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(session_id: UUID, db: DbSession, user: CurrentUser) -> list[MessageOut]:
    """The live branch, with sibling counts so the UI can offer branch switching."""
    session = await _require(db, session_id)
    path = await tree.active_path(db, session)
    out: list[MessageOut] = []
    for m in path:
        idx, count = await tree.sibling_info(db, m)
        out.append(await _message_out(db, m, idx, count))
    return out


@router.get("/{session_id}/messages/{message_id}/branches", response_model=list[BranchOut])
async def list_branches(session_id: UUID, message_id: UUID, db: DbSession,
                        user: CurrentUser) -> list[BranchOut]:
    """Alternatives that share this message's parent — the other paths taken."""
    session = await _require(db, session_id)
    msg = await db.get(Message, message_id)
    if msg is None or msg.session_id != session_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "메시지를 찾을 수 없습니다")

    siblings = (await db.execute(
        select(Message).where(Message.session_id == session_id,
                              Message.parent_id == msg.parent_id,
                              Message.role == msg.role)
        .order_by(Message.created_at)
    )).scalars().all()

    active = {m.id for m in await tree.active_path(db, session)}
    return [
        BranchOut(message_id=s.id, preview=(s.content or "")[:120],
                  created_at=s.created_at, is_active=s.id in active)
        for s in siblings
    ]


@router.post("/{session_id}/revert", response_model=list[MessageOut])
async def revert(session_id: UUID, body: RevertRequest, db: DbSession,
                 user: CurrentUser) -> list[MessageOut]:
    """Move the live path back to a checkpoint.

    This only moves ``active_leaf_id``: everything after the checkpoint stays in
    the database and remains reachable through the branch switcher. Continuing
    from here forks a new branch instead of overwriting what was there.
    """
    session = await _require(db, session_id)
    target = await db.get(Message, body.message_id)
    if target is None or target.session_id != session_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "메시지를 찾을 수 없습니다")

    dropped = await tree.descendants_count(db, target.id)
    session.active_leaf_id = target.id
    await db.flush()

    await realtime.publish_session(str(session_id), "branch.reverted", {
        "session_id": str(session_id), "message_id": str(target.id),
        "by": user.name, "messages_after": dropped,
    })
    return await list_messages(session_id, db, user)


@router.post("/{session_id}/switch/{message_id}", response_model=list[MessageOut])
async def switch_branch(session_id: UUID, message_id: UUID, db: DbSession,
                        user: CurrentUser) -> list[MessageOut]:
    """Make another branch the live one, following it down to its leaf."""
    session = await _require(db, session_id)
    msg = await db.get(Message, message_id)
    if msg is None or msg.session_id != session_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "메시지를 찾을 수 없습니다")

    session.active_leaf_id = await tree.leaf_of(db, message_id)
    await db.flush()
    await realtime.publish_session(str(session_id), "branch.switched", {
        "session_id": str(session_id), "message_id": str(message_id), "by": user.name,
    })
    return await list_messages(session_id, db, user)


@router.post("/{session_id}/messages")
async def send_message(session_id: UUID, body: SendMessage, request: Request,
                       db: DbSession, user: CurrentUser):
    """Post a question and stream the answer.

    Everything is streamed twice: once down this response to the caller, and once
    onto the session's Redis channel so every other viewer sees the same tokens
    arrive. That is what makes a shared session feel like one conversation rather
    than several private ones.
    """
    session = await _require(db, session_id)

    parent_id = body.parent_id or session.active_leaf_id
    branch_root = await tree.branch_root_for(db, parent_id)

    user_msg = Message(
        session_id=session_id, parent_id=parent_id, branch_root_id=branch_root,
        role=MessageRole.user, author_id=user.id, content=body.content.strip(),
        status=MessageStatus.complete,
    )
    db.add(user_msg)
    await db.flush()

    assistant = Message(
        session_id=session_id, parent_id=user_msg.id,
        branch_root_id=branch_root or user_msg.id,
        role=MessageRole.assistant, content="", status=MessageStatus.queued,
    )
    db.add(assistant)
    await db.flush()
    session.active_leaf_id = assistant.id

    if session.title in ("새 대화", "", None):
        session.title = await _title_for(body.content)

    history = await tree.history_for_model(db, session)
    document_ids = await retrieval.effective_document_ids(db, session_id)
    await db.commit()

    await realtime.publish_session(str(session_id), "message", {
        "id": str(user_msg.id), "parent_id": str(parent_id) if parent_id else None,
        "role": "user", "author": {"id": str(user.id), "name": user.name},
        "content": user_msg.content, "status": "complete",
        "created_at": user_msg.created_at.isoformat(),
    })

    return EventSourceResponse(
        _stream_turn(request, session_id, assistant.id, user_msg.content,
                     history, document_ids),
        ping=15000,
    )


async def _stream_turn(request: Request, session_id: UUID, assistant_id: UUID,
                       question: str, history: list[dict], document_ids: list[UUID]):
    """Run the agent, persist the result, and fan every event out to viewers."""
    lock_token = str(uuid.uuid4())
    channel_send = realtime.publish_session

    async with SessionLocal() as db:
        got_lock = await run_lock.acquire(str(session_id), lock_token)
        if not got_lock:
            depth = await run_lock.queue_depth(str(session_id))
            payload = {"message": "이 세션에서 다른 답변이 생성 중입니다. 잠시 후 다시 시도해 주세요.",
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
                    log.info("caller disconnected from session %s", session_id)

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

                payload = json.dumps({"session_id": str(session_id),
                                      "message_id": str(assistant_id), **data},
                                     ensure_ascii=False, default=str)
                yield {"event": ev.type, "data": payload}
                await channel_send(str(session_id), ev.type,
                                   {"message_id": str(assistant_id), **data})

            resolved = await _persist(db, assistant_id, answer, citations,
                                      sources_by_sid, steps, run_id, rejected, error)

            final = {"message_id": str(assistant_id),
                     "citations": [r.as_dict() for r in resolved]}
            yield {"event": "final", "data": json.dumps(final, ensure_ascii=False, default=str)}
            await channel_send(str(session_id), "final", final)

        except Exception as exc:  # noqa: BLE001
            log.exception("turn failed in session %s", session_id)
            msg = await db.get(Message, assistant_id)
            if msg is not None:
                msg.status = MessageStatus.failed
                msg.content = answer
                await db.commit()
            yield {"event": "error", "data": json.dumps({"message": str(exc)},
                                                        ensure_ascii=False)}
        finally:
            await run_lock.release(str(session_id), lock_token)


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


async def _title_for(question: str) -> str:
    from api.app.agent.prompts import ko

    try:
        title = await llm_client.complete(
            [{"role": "system", "content": ko.TITLE_SYSTEM},
             {"role": "user", "content": question}],
            temperature=0.3, max_tokens=40)
        title = title.strip().strip('"\'').splitlines()[0][:60]
        return title or question[:40]
    except Exception:  # noqa: BLE001 - a title is never worth failing a turn over
        return question[:40]


# ------------------------------------------------------------------ events ----
@router.get("/{session_id}/events")
async def session_events(session_id: UUID, request: Request, db: DbSession,
                         user: CurrentUser):
    """Everything happening in this session, for every viewer.

    Includes other people's messages and the assistant tokens they triggered, so
    opening a session someone else is using shows the answer arriving live.
    """
    await _require(db, session_id)

    async def gen():
        await realtime.heartbeat(str(session_id), str(user.id), user.name)
        viewers = await realtime.viewers(str(session_id))
        yield {"event": "presence", "data": json.dumps({"viewers": viewers},
                                                       ensure_ascii=False)}

        beat = asyncio.create_task(_presence_loop(session_id, user))
        try:
            async for event in realtime.subscribe(realtime.session_channel(str(session_id))):
                if await request.is_disconnected():
                    break
                yield {"event": event["event"],
                       "data": json.dumps(event["data"], ensure_ascii=False, default=str)}
        finally:
            beat.cancel()

    return EventSourceResponse(gen(), ping=15000)


async def _presence_loop(session_id: UUID, user) -> None:
    """Refresh this viewer's presence key so "3 viewing" stays accurate."""
    try:
        while True:
            await realtime.heartbeat(str(session_id), str(user.id), user.name)
            await asyncio.sleep(10)
    except asyncio.CancelledError:
        pass


# ----------------------------------------------------------------- helpers ----
async def _require(db, session_id: UUID) -> ChatSession:
    session = (await db.execute(
        select(ChatSession).options(selectinload(ChatSession.creator))
        .where(ChatSession.id == session_id)
    )).scalar_one_or_none()
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다")
    return session


async def _session_out(db, s: ChatSession) -> SessionOut:
    msg_count = await db.scalar(select(func.count()).select_from(Message)
                                .where(Message.session_id == s.id))
    docs = await retrieval.effective_document_ids(db, s.id)
    folder_name = None
    if s.folder_id:
        folder_name = await db.scalar(select(Folder.name).where(Folder.id == s.folder_id))
    return SessionOut(
        id=s.id, title=s.title, folder_id=s.folder_id, folder_name=folder_name,
        created_by=_user_out(s.creator), active_leaf_id=s.active_leaf_id,
        message_count=msg_count or 0, document_count=len(docs),
        created_at=s.created_at, updated_at=s.updated_at,
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
        id=m.id, session_id=m.session_id, parent_id=m.parent_id, role=m.role.value,
        author=_user_out(m.author), content=m.content, status=m.status.value,
        citations=[
            CitationOut(**{**tree.citation_out(c),
                           "filename": docs.get(c.document_id, "")})
            for c in m.citations
        ],
        created_at=m.created_at, sibling_index=sibling_index, sibling_count=sibling_count,
    )
