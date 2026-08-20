"""Async orchestration for table/numeric tool use.

Resolves the DB-backed side (chunk -> table_json) and drives the tool-calling
loop; the pure cell-selection/arithmetic logic lives in
``api.app.agent.tools.tables``.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.agent import citations as cit
from api.app.agent.prompts import ko
from api.app.agent.tools import tables
from api.app.db.models import Chunk, DocumentElement
from api.app.services import llm_client

log = logging.getLogger("agent.tables")


async def load_table_json(db: AsyncSession, chunk_id: int) -> dict | None:
    """``Chunk.element_id -> DocumentElement.table_json``, or ``None`` if the
    chunk has no element or the element carries no table."""
    row = (
        await db.execute(
            select(DocumentElement.table_json)
            .join(Chunk, Chunk.element_id == DocumentElement.id)
            .where(Chunk.id == chunk_id)
        )
    ).first()
    return row[0] if row else None


async def table_node(
    db: AsyncSession, sources: list[cit.SourceRef], question: str
) -> list[cit.SourceRef]:
    """If any source is a table, offer ``query_table``/``calc`` to a
    tool-calling LLM call and append one synthetic ``SourceRef`` per table it
    actually touched. Returns ``sources`` unchanged on any failure or if
    nothing was queried."""
    table_sources = [s for s in sources if s.is_table]
    if not table_sources:
        return sources

    tables_by_sid: dict[int, dict] = {}
    for s in table_sources:
        tj = await load_table_json(db, s.chunk_id)
        if tj:
            tables_by_sid[s.sid] = tj
    if not tables_by_sid:
        return sources

    def _query_table(table_id: int, **kw) -> list[dict]:
        tj = tables_by_sid.get(table_id)
        if tj is None:
            return []
        return tables.query_table(tj, **kw)

    dispatch = {"query_table": _query_table, "calc": tables.calc}
    context = cit.build_context_block(table_sources)
    msgs = [
        {"role": "system", "content": ko.TABLE_SYSTEM},
        {"role": "user", "content": f"[표]\n{context}\n\n[질문]\n{question}"},
    ]
    try:
        text, trace = await llm_client.complete_with_tools(
            msgs, [ko.QUERY_TABLE_TOOL, ko.CALC_TOOL], dispatch, max_tokens=768
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("table tool loop failed (%s); composing from raw table text", exc)
        return sources

    touched = sorted({
        t["args"].get("table_id")
        for t in trace
        if t["tool"] == "query_table" and isinstance(t["args"].get("table_id"), int)
    })
    if not touched:
        return sources

    by_sid = {s.sid: s for s in table_sources}
    new_sid = max(s.sid for s in sources) + 1
    synthetic: list[cit.SourceRef] = []
    for tid in touched:
        origin = by_sid.get(tid)
        if origin is None:
            continue
        lines = [f"[표 계산 결과 — {origin.label()}]"]
        for t in trace:
            if t["tool"] == "query_table" and t["args"].get("table_id") == tid:
                lines.append(f"조회: {t['args']} → {t['result']}")
            elif t["tool"] == "calc":
                lines.append(f"계산: {t['args'].get('expression')} = {t['result']}")
        if text:
            lines.append(f"결론: {text}")
        synthetic.append(cit.SourceRef(
            sid=new_sid,
            chunk_id=origin.chunk_id,
            document_id=origin.document_id,
            filename=origin.filename,
            text="\n".join(lines),
            heading_path=origin.heading_path,
            page_from=origin.page_from,
            page_to=origin.page_to,
            is_table=True,
            out_of_scope=origin.out_of_scope,
        ))
        new_sid += 1

    return sources + synthetic
