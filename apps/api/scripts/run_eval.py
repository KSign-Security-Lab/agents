"""Citation-accuracy eval harness (``make eval``).

Runs every case in ``eval_gold_set.GOLD_CASES`` through the real agent turn
(``run_agent``) against already-ingested sample documents, resolves citation
geometry the same way a live turn would (mirroring
``api.app.routers.sessions._stream_turn``/``_persist``), and scores the
result. Read-only: nothing is written to the DB, so repeated runs never
pollute the sessions tables the product UI reads from.

Prerequisites: a live stack (``make up``) and the gold set's documents
already ingested (``make samples && make ingest FILE=samples/<name>``) —
this script has no filesystem access to the sample files themselves and no
offline/mock mode for the LLM.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from api.app.agent import citations as cit
from api.app.agent.graph import run_agent
from api.app.agent.resolve import resolve_citations
from api.app.db.models import Chunk, DocStatus, Document
from api.app.db.session import SessionLocal
from api.scripts.eval_gold_set import GOLD_CASES


async def _resolve_document(db, filename: str) -> Document | None:
    return (
        await db.execute(
            select(Document)
            .where(Document.filename == filename, Document.status == DocStatus.ready)
            .order_by(Document.created_at.desc())
        )
    ).scalars().first()


def _rehydrate(data: dict) -> cit.Citation:
    """Rebuild a Citation from a streamed event payload (mirrors
    api.app.routers.sessions._rehydrate)."""
    return cit.Citation(
        idx=data["idx"],
        source=cit.SourceRef(
            sid=data["sid"], chunk_id=data.get("chunk_id") or 0,
            document_id=data["document_id"], filename=data.get("filename", ""),
            text="", out_of_scope=bool(data.get("out_of_scope")),
        ),
    )


async def _hydrate_chunk_text(db, citations: list[cit.Citation]) -> None:
    """Fill in the chunk text/page the resolver needs to align sentences
    (mirrors api.app.routers.sessions._persist)."""
    chunk_ids = [c.source.chunk_id for c in citations if c.source.chunk_id]
    if not chunk_ids:
        return
    rows = (
        await db.execute(
            select(Chunk.id, Chunk.text, Chunk.heading_path, Chunk.page_from,
                   Chunk.page_to, Chunk.t_start_ms, Chunk.t_end_ms)
            .where(Chunk.id.in_(chunk_ids))
        )
    ).all()
    by_id = {r[0]: r for r in rows}
    for c in citations:
        row = by_id.get(c.source.chunk_id)
        if row:
            c.source.text = row[1] or ""
            c.source.heading_path = row[2]
            c.source.page_from, c.source.page_to = row[3], row[4]
            c.source.t_start_ms, c.source.t_end_ms = row[5], row[6]


def _score(case: dict, clean_answer: str, resolved: list, rejected: list[int]) -> list[str]:
    reasons: list[str] = []

    for needle in case.get("expect_answer_contains", []):
        if needle not in clean_answer:
            reasons.append(f"answer missing {needle!r}")

    if case.get("expect_no_citations"):
        if resolved:
            reasons.append(f"expected no citations, got {len(resolved)}")
    elif "expect_citation" in case:
        exp = case["expect_citation"]
        matched = False
        for r in resolved:
            if r.filename != exp["filename"]:
                continue
            if "page_in" in exp:
                lo, hi = exp["page_in"]
                if r.page_no is not None and lo <= r.page_no <= hi:
                    matched = True
                    break
            elif "text_contains" in exp:
                if exp["text_contains"] in (r.snippet or ""):
                    matched = True
                    break
            else:
                matched = True
                break
        if not matched:
            reasons.append(f"no citation matched {exp}")

    max_rejected = case.get("max_rejected", 0)
    if len(rejected) > max_rejected:
        reasons.append(f"rejected citations {len(rejected)} > max {max_rejected}")

    return reasons


async def _run_case(db, case: dict, doc_by_filename: dict[str, Document]) -> dict:
    document_ids = [doc_by_filename[fn].id for fn in case["document_filenames"]] or None

    answer = ""
    citations: list[cit.Citation] = []
    rejected: list[int] = []

    async for ev in run_agent(db, question=case["question"], history=[],
                              document_ids=document_ids):
        data = ev.data
        if ev.type == "token":
            answer += data.get("text", "")
        elif ev.type == "citation":
            citations.append(_rehydrate(data))
        elif ev.type == "revision":
            answer = data.get("text", answer)
            for c in data.get("citations", []):
                citations.append(_rehydrate(c))
        elif ev.type == "done":
            rejected = data.get("rejected") or []
        elif ev.type == "error":
            return {"id": case["id"], "passed": False,
                    "reasons": [f"agent error: {data.get('message')}"]}

    await _hydrate_chunk_text(db, citations)
    resolved = await resolve_citations(db, answer, citations)
    clean = cit.strip_cite_tokens(answer)
    reasons = _score(case, clean, resolved, rejected)

    return {"id": case["id"], "passed": not reasons, "reasons": reasons}


def _print_summary(results: list[dict]) -> None:
    passed = sum(1 for r in results if r["passed"])
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"{status} {r['id']}")
        for reason in r["reasons"]:
            print(f"     - {reason}")
    print(f"\n{passed}/{len(results)} passed")


async def main() -> None:
    async with SessionLocal() as db:
        filenames = {fn for case in GOLD_CASES for fn in case["document_filenames"]}
        doc_by_filename: dict[str, Document] = {}
        missing: list[str] = []
        for fn in sorted(filenames):
            doc = await _resolve_document(db, fn)
            if doc is None:
                missing.append(fn)
            else:
                doc_by_filename[fn] = doc

        if missing:
            print("FAIL: gold-set document(s) not ingested:")
            for fn in missing:
                print(f"  {fn}  ->  make ingest FILE=samples/{fn}")
            raise SystemExit(2)

        results = [await _run_case(db, case, doc_by_filename) for case in GOLD_CASES]

        _print_summary(results)
        raise SystemExit(0 if all(r["passed"] for r in results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
