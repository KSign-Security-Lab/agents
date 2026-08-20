"""The message tree.

Channels are shared and revertible, so history is a tree rather than a list:

* Every message has a ``parent_id``. Sending normally appends to the channel's
  ``active_leaf_id``; reverting just moves that pointer, and the next message
  becomes a *sibling* of whatever came after it before.
* Nothing is ever deleted. An earlier path stays readable, which matters when
  the person who wrote it is not the person who reverted.
* ``branch_root_id`` labels the branch. It isn't read anywhere yet — it's the
  key a future LangGraph checkpointer would use to give two branches of the
  same channel independent agent state.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.app.db.models import Channel, Citation, Message, MessageRole


async def path_to(db: AsyncSession, message_id: UUID) -> list[Message]:
    """The chain from the root down to ``message_id``, oldest first.

    Done as one recursive CTE rather than a loop of gets: a long conversation
    would otherwise cost one round trip per turn on every request.
    """
    rows = (await db.execute(
        select(Message)
        .options(selectinload(Message.author), selectinload(Message.citations))
        .where(Message.id.in_(
            select(_ancestors_cte(message_id).c.id)
        ))
        .order_by(Message.created_at)
    )).scalars().unique().all()
    return list(rows)


def _ancestors_cte(message_id: UUID):

    base = (
        select(Message.id, Message.parent_id)
        .where(Message.id == message_id)
        .cte("ancestors", recursive=True)
    )
    parent = select(Message.id, Message.parent_id).join(
        base, Message.id == base.c.parent_id
    )
    return base.union_all(parent)


async def active_path(db: AsyncSession, channel: Channel) -> list[Message]:
    """Messages on the channel's live branch."""
    if channel.active_leaf_id is None:
        return []
    return await path_to(db, channel.active_leaf_id)


async def history_for_model(db: AsyncSession, channel: Channel, *,
                            limit: int = 12) -> list[dict[str, str]]:
    """Recent turns on the live branch, as chat messages.

    Two things happen here that are easy to get wrong:

    * Author names are prefixed on user turns. In a shared channel the model
      otherwise cannot tell that two consecutive questions came from different
      people, and answers as if one person contradicted themselves.
    * Citation tokens are stripped from assistant turns. Stored content holds
      ``[[cite:1]]`` markers, and a model shown them will imitate that syntax on
      the next turn — emitting ``[[cite:S1]]`` instead of the ``[S1]`` the parser
      recognises, so every follow-up answer loses its citations. The S-ids are
      per-turn anyway, so old ones are meaningless in a new turn.
    """
    from api.app.agent.citations import strip_cite_tokens

    messages = await active_path(db, channel)
    out: list[dict[str, str]] = []
    for m in messages[-limit:]:
        if m.role == MessageRole.assistant:
            out.append({"role": "assistant", "content": strip_cite_tokens(m.content)})
        elif m.role == MessageRole.user:
            who = m.author.name if m.author else None
            content = f"[{who}] {m.content}" if who else m.content
            out.append({"role": "user", "content": content})
    return out


async def sibling_info(db: AsyncSession, message: Message) -> tuple[int, int]:
    """``(index, count)`` among messages sharing this parent — the branch switcher."""
    siblings = (await db.execute(
        select(Message.id).where(
            Message.channel_id == message.channel_id,
            Message.parent_id == message.parent_id,
            Message.role == message.role,
        ).order_by(Message.created_at)
    )).scalars().all()
    try:
        return siblings.index(message.id), len(siblings)
    except ValueError:
        return 0, max(1, len(siblings))


async def branch_root_for(db: AsyncSession, parent_id: UUID | None) -> UUID | None:
    """The branch a new child belongs to.

    A message that is not the first child starts a new branch; otherwise it
    continues its parent's.
    """
    if parent_id is None:
        return None
    parent = await db.get(Message, parent_id)
    if parent is None:
        return None
    existing = await db.scalar(
        select(Message.id).where(Message.parent_id == parent_id).limit(1)
    )
    return parent_id if existing is not None else (parent.branch_root_id or parent_id)


async def leaf_of(db: AsyncSession, message_id: UUID) -> UUID:
    """Follow first-children down to a leaf, used when switching branches."""
    current = message_id
    while True:
        child = await db.scalar(
            select(Message.id).where(Message.parent_id == current)
            .order_by(Message.created_at).limit(1)
        )
        if child is None:
            return current
        current = child


async def descendants_count(db: AsyncSession, message_id: UUID) -> int:
    from sqlalchemy import text as sql

    return int(await db.scalar(sql("""
        WITH RECURSIVE d AS (
            SELECT id FROM messages WHERE parent_id = :mid
            UNION ALL
            SELECT m.id FROM messages m JOIN d ON m.parent_id = d.id
        )
        SELECT count(*) FROM d
    """), {"mid": str(message_id)}) or 0)


def citation_out(c: Citation) -> dict:
    return {
        "idx": c.idx,
        "document_id": str(c.document_id),
        "chunk_id": c.chunk_id,
        "page_no": c.page_no,
        "rects": c.rects or [],
        "t_start_ms": c.t_start_ms,
        "t_end_ms": c.t_end_ms,
        "snippet": c.snippet,
        "heading_path": c.heading_path,
        "out_of_scope": c.out_of_scope,
    }
