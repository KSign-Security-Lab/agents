"""The answering agent.

Shape:

    plan -> [researcher x N in parallel] -> merge -> tables -> compose -> verify

This is hand-rolled asyncio, not a LangGraph graph: ``researcher`` is a
sub-agent fanned out one per sub-question via ``asyncio.gather``, and each runs
its own retrieve/rerank/grade loop, escalating to a whole-corpus search when
the session's selected documents come up short. The fan-out is what makes
multi-hop questions ("compare the payment terms in these three contracts")
answerable without stuffing everything into one retrieval.

Streaming is handled outside the graph: ``run_agent`` yields events so the API
can push tokens, citations and step traces to every viewer of the session.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.app.agent import citations as cit
from api.app.agent.nodes import tables as tables_node_mod
from api.app.agent.nodes import verify as verify_node_mod
from api.app.agent.prompts import ko
from api.app.config import settings
from api.app.services import llm_client, retrieval
from api.app.services.retrieval import Hit

log = logging.getLogger("agent.graph")


# ---------------------------------------------------------------- events ----
@dataclass(slots=True)
class Event:
    """One thing worth telling the client about."""

    type: Literal["step", "token", "citation", "title", "revision", "done", "error"]
    data: dict[str, Any]


@dataclass(slots=True)
class Plan:
    intent: str = "doc_qa"
    subqueries: list[str] = field(default_factory=list)
    needs_tables: bool = False


@dataclass(slots=True)
class Finding:
    subquery: str
    hits: list[Hit]
    iterations: int
    escalated: bool


# ------------------------------------------------------------------ nodes ---
async def plan_node(question: str, history: list[dict[str, str]]) -> Plan:
    """Decompose the question.

    Uses guided JSON rather than tool calls so it works on any served model,
    including one with no vLLM tool-call parser.
    """
    msgs = [
        {"role": "system", "content": ko.PLAN_SYSTEM.format(max_subqueries=settings.max_subagents)},
        *history[-4:],
        {"role": "user", "content": question},
    ]
    try:
        raw = await llm_client.complete_json(msgs, ko.PLAN_SCHEMA, max_tokens=512)
        plan = Plan(
            intent=raw.get("intent", "doc_qa"),
            subqueries=[s.strip() for s in raw.get("subqueries", []) if s.strip()],
            needs_tables=bool(raw.get("needs_tables")),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("planner failed (%s); searching the question verbatim", exc)
        plan = Plan(intent="doc_qa", subqueries=[question])

    if plan.intent != "chitchat" and not plan.subqueries:
        plan.subqueries = [question]
    return Plan(
        intent=plan.intent,
        subqueries=plan.subqueries[: settings.max_subagents],
        needs_tables=plan.needs_tables,
    )


async def researcher_node(db: AsyncSession, subquery: str,
                          document_ids: list[UUID] | None) -> Finding:
    """One sub-agent: retrieve, rerank, judge, and retry or widen if thin."""
    query = subquery
    escalated = False
    hits: list[Hit] = []

    for iteration in range(1, settings.max_retrieve_iterations + 1):
        found = await retrieval.hybrid_search(db, query, document_ids=document_ids)
        found = await retrieval.rerank_hits(query, found)
        hits = retrieval.dedupe_adjacent(found, max_per_document=4)

        if not hits and document_ids and settings.corpus_escalation and not escalated:
            # Nothing in the selected documents — look at the whole corpus and
            # mark the results so the answer can say where they came from.
            escalated = True
            widened = await retrieval.hybrid_search(db, query, document_ids=None)
            widened = await retrieval.rerank_hits(query, widened)
            for h in widened:
                h.out_of_scope = True
            hits = retrieval.dedupe_adjacent(widened, max_per_document=2)

        if iteration >= settings.max_retrieve_iterations or not hits:
            break

        grade = await _grade(subquery, hits)
        if grade.get("sufficient", True):
            break

        next_query = (grade.get("next_query") or "").strip()
        if not next_query or next_query == query:
            if document_ids and settings.corpus_escalation and not escalated:
                escalated = True
                document_ids = None  # widen for the next iteration
                continue
            break
        query = next_query

    return Finding(subquery=subquery, hits=hits, iterations=iteration, escalated=escalated)


async def _grade(subquery: str, hits: list[Hit]) -> dict[str, Any]:
    preview = "\n\n".join(f"- {h.filename}: {h.text[:400]}" for h in hits[:5])
    msgs = [
        {"role": "system", "content": ko.GRADE_SYSTEM},
        {"role": "user", "content": f"[질문]\n{subquery}\n\n[검색 결과]\n{preview}"},
    ]
    try:
        return await llm_client.complete_json(msgs, ko.GRADE_SCHEMA, max_tokens=256)
    except Exception as exc:  # noqa: BLE001
        log.warning("grader failed (%s); accepting current results", exc)
        return {"sufficient": True, "missing": "", "next_query": ""}


def merge_findings(findings: list[Finding], *, limit: int | None = None) -> list[cit.SourceRef]:
    """Interleave each sub-agent's best hits, then assign the S-ids.

    Interleaving matters: taking the top-N globally would let one sub-question
    monopolise the context and leave a multi-part question half-answered.
    """
    limit = limit or settings.rerank_top_k
    queues = [list(f.hits) for f in findings if f.hits]
    ordered: list[Hit] = []
    seen: set[int] = set()
    while queues and len(ordered) < limit:
        for q in list(queues):
            if not q:
                queues.remove(q)
                continue
            h = q.pop(0)
            if h.chunk_id in seen:
                continue
            seen.add(h.chunk_id)
            ordered.append(h)
            if len(ordered) >= limit:
                break

    return [
        cit.SourceRef(
            sid=i + 1,
            chunk_id=h.chunk_id,
            document_id=h.document_id,
            filename=h.filename,
            text=h.text,
            heading_path=h.heading_path,
            page_from=h.page_from,
            page_to=h.page_to,
            t_start_ms=h.t_start_ms,
            t_end_ms=h.t_end_ms,
            score=h.rerank_score if h.rerank_score is not None else h.score,
            is_table=h.is_table,
            out_of_scope=h.out_of_scope,
        )
        for i, h in enumerate(ordered)
    ]


# ------------------------------------------------------------------ driver --
async def run_agent(db: AsyncSession, *, question: str,
                    history: list[dict[str, str]],
                    document_ids: list[UUID] | None) -> AsyncIterator[Event]:
    """Run one turn, yielding events as they happen.

    The caller persists the message, fans events out over Redis and resolves
    citation geometry; this function stays concerned only with the reasoning.
    """
    t0 = time.perf_counter()
    run_id = str(uuid.uuid4())
    step = 0

    def _step(node: str, label: str, **out: Any) -> Event:
        nonlocal step
        step += 1
        return Event("step", {"run_id": run_id, "ord": step, "node": node,
                              "label": label, "output": out})

    try:
        # ---- plan -------------------------------------------------------
        plan = await plan_node(question, history)
        yield _step("plan", f"질문 분해 ({len(plan.subqueries)}개)",
                    intent=plan.intent, subqueries=plan.subqueries)

        if plan.intent == "chitchat":
            async for tok in llm_client.stream(
                [{"role": "system", "content": ko.ANSWER_SYSTEM}, *history[-6:],
                 {"role": "user", "content": question}], temperature=0.5):
                yield Event("token", {"text": tok})
            yield Event("done", {"run_id": run_id, "citations": [],
                                 "latency_ms": int((time.perf_counter() - t0) * 1000)})
            return

        # ---- research fan-out -------------------------------------------
        yield _step("research", f"문서 검색 중 ({len(plan.subqueries)}개 하위 질의 병렬)")
        findings = await asyncio.gather(
            *(researcher_node(db, sq, document_ids) for sq in plan.subqueries),
            return_exceptions=True,
        )
        ok: list[Finding] = []
        for sq, f in zip(plan.subqueries, findings):
            if isinstance(f, Exception):
                log.warning("researcher failed for %r: %s", sq, f)
                continue
            ok.append(f)
            yield _step("researcher", f"'{sq}' → {len(f.hits)}건",
                        subquery=sq, hits=len(f.hits), escalated=f.escalated)

        sources = merge_findings(ok)
        if not sources:
            yield Event("token", {"text": ko.NO_CONTEXT_ANSWER})
            yield Event("done", {"run_id": run_id, "citations": [],
                                 "latency_ms": int((time.perf_counter() - t0) * 1000)})
            return

        yield _step("merge", f"근거 {len(sources)}건 선정",
                    out_of_scope=sum(1 for s in sources if s.out_of_scope))

        # ---- table tools --------------------------------------------------
        if plan.needs_tables and any(s.is_table for s in sources):
            before = len(sources)
            sources = await tables_node_mod.table_node(db, sources, question)
            if len(sources) > before:
                yield _step("tables", f"표에서 수치 {len(sources) - before}건 계산",
                            added=len(sources) - before)

        # ---- compose ----------------------------------------------------
        context = cit.build_context_block(sources)
        msgs = [
            {"role": "system", "content": ko.ANSWER_SYSTEM},
            *history[-6:],
            {"role": "user", "content": ko.ANSWER_USER.format(context=context, question=question)},
        ]

        parser = cit.StreamingCitationParser(sources)
        parts: list[str] = []
        async for delta in llm_client.stream(msgs, temperature=0.2):
            text, new_cits = parser.feed(delta)
            if text:
                parts.append(text)
                yield Event("token", {"text": text})
            for c in new_cits:
                yield Event("citation", {"idx": c.idx, "sid": c.source.sid,
                                         "chunk_id": c.source.chunk_id,
                                         "document_id": c.source.document_id,
                                         "filename": c.source.filename,
                                         "out_of_scope": c.source.out_of_scope})
        tail, new_cits = parser.finish()
        if tail:
            parts.append(tail)
            yield Event("token", {"text": tail})
        for c in new_cits:
            yield Event("citation", {"idx": c.idx, "sid": c.source.sid,
                                     "chunk_id": c.source.chunk_id,
                                     "document_id": c.source.document_id,
                                     "filename": c.source.filename,
                                     "out_of_scope": c.source.out_of_scope})

        if parser.rejected:
            yield _step("compose", f"근거 없는 참조 {len(parser.rejected)}건 제거",
                        rejected=[r.sid for r in parser.rejected])

        # ---- verify -------------------------------------------------------
        answer = "".join(parts)
        result = await verify_node_mod.verify_node(answer, sources, parser)
        if result.changed:
            yield _step("verify", f"근거 보강 {result.fixed_sentences}건 반영",
                        fixed=result.fixed_sentences)
            yield Event("revision", {
                "run_id": run_id,
                "text": result.text,
                "citations": [
                    {"idx": c.idx, "sid": c.source.sid, "chunk_id": c.source.chunk_id,
                     "document_id": c.source.document_id, "filename": c.source.filename,
                     "out_of_scope": c.source.out_of_scope}
                    for c in result.new_citations
                ],
            })

        yield Event("done", {
            "run_id": run_id,
            "citations": [{"idx": c.idx, "sid": c.source.sid} for c in parser.citations],
            "rejected": [r.sid for r in parser.rejected],
            "sources": [{"sid": s.sid, "chunk_id": s.chunk_id, "document_id": s.document_id}
                        for s in sources],
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        })

    except Exception as exc:  # noqa: BLE001
        log.exception("agent run failed")
        yield Event("error", {"run_id": run_id, "message": str(exc)})


def parser_for(sources: list[cit.SourceRef]) -> cit.StreamingCitationParser:
    """Exposed for tests and for the verify pass, which re-parses a revision."""
    return cit.StreamingCitationParser(sources)
