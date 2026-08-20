"""Hybrid retrieval over pgvector.

bge-m3 produces a dense vector and sparse lexical weights in one pass, so both
halves of a hybrid search live in the same Postgres table:

* dense   -> ``vector(1024)``    cosine, HNSW
* sparse  -> ``sparsevec(250002)`` max inner product, HNSW

Using the model's own sparse weights instead of Postgres full-text search
matters for Korean specifically: ``to_tsvector`` has no Korean stemmer, and
Korean is agglutinative, so lexical matching via tsvector would mostly fail on
inflected forms. The model's tokenizer handles that for us.

The two result lists are combined with Reciprocal Rank Fusion, which needs no
score calibration between two very differently-scaled similarity measures.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import settings
from api.app.services.infer_client import infer_client

log = logging.getLogger("services.retrieval")

RRF_K = 60


@dataclass(slots=True)
class Hit:
    chunk_id: int
    document_id: str
    filename: str
    text: str
    heading_path: str | None
    page_from: int | None
    page_to: int | None
    t_start_ms: int | None
    t_end_ms: int | None
    is_table: bool
    score: float
    dense_rank: int | None = None
    sparse_rank: int | None = None
    rerank_score: float | None = None
    out_of_scope: bool = False


def to_sparsevec(weights: dict[int, float] | dict[str, float], dim: int) -> str:
    """pgvector's ``sparsevec`` *text* form: ``{index:value,...}/dim``, 1-based.

    For raw SQL parameters that are cast with ``CAST(:x AS sparsevec)``. JSON
    round-trips turn the model's integer token ids into strings, so keys are
    coerced here rather than at every call site.
    """
    if not weights:
        return "{}/" + str(dim)
    items = ",".join(f"{int(i) + 1}:{float(v):.6g}"
                     for i, v in sorted(((int(k), v) for k, v in weights.items())))
    return f"{{{items}}}/{dim}"


def to_sparse_vector(weights: dict[int, float] | dict[str, float], dim: int):
    """A ``SparseVector`` for assignment to an ORM ``SPARSEVEC`` column.

    The text form above is only understood by an explicit SQL cast; assigning it
    to a mapped column makes the driver try to parse "{" as a float.
    """
    from pgvector import SparseVector

    return SparseVector({int(k): float(v) for k, v in weights.items()}, dim)


async def effective_document_ids(db: AsyncSession, session_id: UUID) -> list[UUID]:
    """A session's document scope.

    ``folder documents ∪ session additions − session removals`` — so a session
    can start from a folder and still be narrowed or widened per conversation.
    """
    rows = await db.execute(
        text("""
            WITH folder_docs AS (
                SELECT fd.document_id
                FROM sessions s
                JOIN folder_documents fd ON fd.folder_id = s.folder_id
                WHERE s.id = :sid
            ),
            added AS (
                SELECT document_id FROM session_documents
                WHERE session_id = :sid AND mode = 'add'
            ),
            removed AS (
                SELECT document_id FROM session_documents
                WHERE session_id = :sid AND mode = 'remove'
            )
            SELECT document_id FROM (
                SELECT document_id FROM folder_docs
                UNION
                SELECT document_id FROM added
            ) u
            WHERE document_id NOT IN (SELECT document_id FROM removed)
        """),
        {"sid": str(session_id)},
    )
    return [r[0] for r in rows.fetchall()]


_SEARCH_SQL = """
WITH dense AS (
    SELECT c.id,
           ROW_NUMBER() OVER (ORDER BY c.embedding <=> CAST(:qvec AS vector)) AS rnk
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.embedding IS NOT NULL
      AND d.status = 'ready'
      {scope_filter}
    ORDER BY c.embedding <=> CAST(:qvec AS vector)
    LIMIT :cand
),
sparse AS (
    SELECT c.id,
           ROW_NUMBER() OVER (ORDER BY c.sparse <#> CAST(:qsparse AS sparsevec)) AS rnk
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.sparse IS NOT NULL
      AND d.status = 'ready'
      {scope_filter}
    ORDER BY c.sparse <#> CAST(:qsparse AS sparsevec)
    LIMIT :cand
),
fused AS (
    SELECT id,
           SUM(rrf) AS score,
           MAX(dense_rank) AS dense_rank,
           MAX(sparse_rank) AS sparse_rank
    FROM (
        SELECT id, 1.0 / (:rrf_k + rnk) AS rrf, rnk AS dense_rank, NULL::bigint AS sparse_rank
        FROM dense
        UNION ALL
        SELECT id, 1.0 / (:rrf_k + rnk) AS rrf, NULL::bigint AS dense_rank, rnk AS sparse_rank
        FROM sparse
    ) x
    GROUP BY id
)
SELECT c.id, c.document_id, d.filename, c.text, c.heading_path,
       c.page_from, c.page_to, c.t_start_ms, c.t_end_ms, c.is_table,
       f.score, f.dense_rank, f.sparse_rank
FROM fused f
JOIN chunks c ON c.id = f.id
JOIN documents d ON d.id = c.document_id
ORDER BY f.score DESC
LIMIT :top_k
"""


async def hybrid_search(db: AsyncSession, query: str, *,
                        document_ids: list[UUID] | None = None,
                        top_k: int | None = None,
                        candidates: int | None = None) -> list[Hit]:
    """Dense + sparse search fused by RRF.

    ``document_ids=None`` searches the whole corpus; an empty list is treated as
    "no documents in scope" and short-circuits, because interpolating an empty
    IN () would silently widen the search instead of narrowing it.
    """
    if document_ids is not None and len(document_ids) == 0:
        return []

    top_k = top_k or settings.retrieve_top_k
    candidates = candidates or max(top_k * 3, 100)

    dense_vec, sparse_w = await infer_client.embed_one(query, kind="query", with_sparse=True)

    scope_filter = ""
    params: dict = {
        "qvec": str(dense_vec),
        "qsparse": to_sparsevec(sparse_w, settings.sparse_dim),
        "cand": candidates,
        "top_k": top_k,
        "rrf_k": RRF_K,
    }
    if document_ids is not None:
        scope_filter = "AND c.document_id = ANY(CAST(:doc_ids AS uuid[]))"
        params["doc_ids"] = [str(d) for d in document_ids]

    sql = _SEARCH_SQL.format(scope_filter=scope_filter)
    rows = (await db.execute(text(sql), params)).fetchall()

    return [
        Hit(
            chunk_id=r[0], document_id=str(r[1]), filename=r[2], text=r[3],
            heading_path=r[4], page_from=r[5], page_to=r[6],
            t_start_ms=r[7], t_end_ms=r[8], is_table=r[9],
            score=float(r[10]), dense_rank=r[11], sparse_rank=r[12],
        )
        for r in rows
    ]


async def rerank_hits(query: str, hits: list[Hit], *, top_k: int | None = None) -> list[Hit]:
    """Cross-encoder rerank. Degrades to the fused order if the sidecar is down —
    a slightly worse ordering is preferable to failing the turn."""
    if not hits:
        return []
    top_k = top_k or settings.rerank_top_k
    try:
        scores = await infer_client.rerank(query, [h.text for h in hits])
    except Exception as exc:  # noqa: BLE001
        log.warning("rerank unavailable (%s); keeping RRF order", exc)
        return hits[:top_k]

    for h, s in zip(hits, scores):
        h.rerank_score = float(s)
    return sorted(hits, key=lambda h: h.rerank_score or 0.0, reverse=True)[:top_k]


def dedupe_adjacent(hits: list[Hit], *, max_per_document: int | None = None) -> list[Hit]:
    """Drop duplicate chunks and optionally cap how much of the context one
    document may occupy, so a single long file cannot crowd out the others."""
    seen: set[int] = set()
    per_doc: dict[str, int] = {}
    out: list[Hit] = []
    for h in hits:
        if h.chunk_id in seen:
            continue
        if max_per_document is not None:
            n = per_doc.get(h.document_id, 0)
            if n >= max_per_document:
                continue
            per_doc[h.document_id] = n + 1
        seen.add(h.chunk_id)
        out.append(h)
    return out
