"""Turn validated citation markers into something the viewer can highlight.

A retrieval chunk is ~512 tokens, which on a page is often several paragraphs.
Highlighting all of it would technically be "the source" but would not show the
reader *which sentence* backed the claim. So for each citation we:

  1. recover the claim sentence the marker was attached to,
  2. split the cited chunk into sentences,
  3. pick the chunk sentences that actually match the claim, by embedding
     similarity — no extra LLM call, so this costs no answer latency,
  4. map those sentence offsets back to page geometry through ``chunk_spans``.

Step 4 is only possible because extraction persisted an offset→bbox map; without
it the best we could do is "somewhere on page 14".
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.agent.citations import CITE_TOKEN, Citation
from api.app.config import settings
from api.app.db.models import ChunkSpan
from api.app.services import sentences as sent
from api.app.services.infer_client import infer_client

log = logging.getLogger("agent.resolve")

MAX_SENTENCES_PER_CITATION = 3
MAX_SNIPPET_CHARS = 400


@dataclass(slots=True)
class Rect:
    page_no: int
    bbox: list[float]  # [x0, y0, x1, y1] in PDF points, page coordinate space

    def as_dict(self) -> dict:
        return {"page_no": self.page_no, "bbox": self.bbox}


@dataclass(slots=True)
class ResolvedCitation:
    idx: int
    document_id: str
    chunk_id: int | None
    filename: str
    page_no: int | None = None
    rects: list[Rect] = field(default_factory=list)
    t_start_ms: int | None = None
    t_end_ms: int | None = None
    snippet: str = ""
    heading_path: str | None = None
    score: float | None = None
    out_of_scope: bool = False

    def as_dict(self) -> dict:
        return {
            "idx": self.idx,
            "document_id": str(self.document_id),
            "chunk_id": self.chunk_id,
            "filename": self.filename,
            "page_no": self.page_no,
            "rects": [r.as_dict() for r in self.rects],
            "t_start_ms": self.t_start_ms,
            "t_end_ms": self.t_end_ms,
            "snippet": self.snippet,
            "heading_path": self.heading_path,
            "score": self.score,
            "out_of_scope": self.out_of_scope,
        }


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / math.sqrt(na * nb)


def claim_for_citation(content: str, idx: int) -> str:
    """The sentence in the answer that carries pill ``idx``.

    Falls back to the whole answer when the token cannot be located, which keeps
    resolution working rather than dropping the citation.
    """
    token = CITE_TOKEN.format(idx=idx)
    pos = content.find(token)
    if pos < 0:
        return content
    found = sent.sentence_containing(content, pos)
    claim = found[2] if found else content
    # The pill tokens themselves are noise for a similarity comparison.
    from api.app.agent.citations import strip_cite_tokens

    return strip_cite_tokens(claim).strip() or content


async def resolve_citations(db: AsyncSession, content: str,
                            citations: list[Citation]) -> list[ResolvedCitation]:
    if not citations:
        return []

    # ---- 1. claims and candidate sentences -------------------------------
    claims: list[str] = []
    candidates: list[list[tuple[int, int, str]]] = []
    for c in citations:
        claims.append(claim_for_citation(content, c.idx))
        candidates.append(sent.split_with_offsets(c.source.text))

    # ---- 2. one batched embedding call for everything ---------------------
    flat: list[str] = list(claims)
    slices: list[tuple[int, int]] = []
    for cand in candidates:
        start = len(flat)
        flat.extend(t for _, _, t in cand)
        slices.append((start, len(flat)))

    vectors: list[list[float]] = []
    try:
        out = await infer_client.embed(flat, kind="query", with_sparse=False)
        vectors = out["dense"]
    except Exception as exc:  # noqa: BLE001 - degrade, never fail an answer
        log.warning("sentence alignment unavailable (%s); falling back to whole-chunk rects", exc)

    # ---- 3. pick the supporting sentences --------------------------------
    chunk_ids = [c.source.chunk_id for c in citations if c.source.chunk_id]
    spans_by_chunk = await _load_spans(db, chunk_ids)

    resolved: list[ResolvedCitation] = []
    for i, c in enumerate(citations):
        src = c.source
        cand = candidates[i]
        picked: list[tuple[int, int, str]] = []

        if vectors and cand:
            cv = vectors[i]
            lo, hi = slices[i]
            scored = [
                (_cosine(cv, vectors[j]), cand[j - lo])
                for j in range(lo, hi)
            ]
            scored.sort(key=lambda t: t[0], reverse=True)
            picked = [s for score, s in scored[:MAX_SENTENCES_PER_CITATION]
                      if score >= settings.sentence_align_threshold]
            # Always keep the single best sentence: a citation with no highlight
            # is worse than a slightly imprecise one.
            if not picked and scored:
                picked = [scored[0][1]]
            # Restore reading order so the snippet reads naturally.
            picked.sort(key=lambda s: s[0])

        spans = spans_by_chunk.get(src.chunk_id or -1, [])
        rects, t_start, t_end = _geometry(spans, picked)

        if picked:
            snippet = " ".join(t.strip() for _, _, t in picked)
        else:
            snippet = src.text.strip()
        if len(snippet) > MAX_SNIPPET_CHARS:
            snippet = snippet[:MAX_SNIPPET_CHARS].rstrip() + " …"

        resolved.append(
            ResolvedCitation(
                idx=c.idx,
                document_id=src.document_id,
                chunk_id=src.chunk_id,
                filename=src.filename,
                page_no=(rects[0].page_no if rects else src.page_from),
                rects=rects,
                t_start_ms=t_start if t_start is not None else src.t_start_ms,
                t_end_ms=t_end if t_end is not None else src.t_end_ms,
                snippet=snippet,
                heading_path=src.heading_path,
                score=src.score,
                out_of_scope=src.out_of_scope,
            )
        )
    return resolved


async def _load_spans(db: AsyncSession, chunk_ids: list[int]) -> dict[int, list[ChunkSpan]]:
    if not chunk_ids:
        return {}
    rows = (
        await db.execute(
            select(ChunkSpan)
            .where(ChunkSpan.chunk_id.in_(set(chunk_ids)))
            .order_by(ChunkSpan.chunk_id, ChunkSpan.text_start)
        )
    ).scalars().all()
    out: dict[int, list[ChunkSpan]] = {}
    for r in rows:
        out.setdefault(r.chunk_id, []).append(r)
    return out


def _geometry(spans: list[ChunkSpan],
              picked: list[tuple[int, int, str]]) -> tuple[list[Rect], int | None, int | None]:
    """Spans overlapping the picked sentences become highlight rectangles.

    One rect per span (spans are line-granular), so the result looks like a text
    selection rather than one giant block over the paragraph.
    """
    if not spans:
        return [], None, None

    if picked:
        wanted = [(s, e) for s, e, _ in picked]
        hits = [sp for sp in spans if any(sp.text_start < e and sp.text_end > s
                                          for s, e in wanted)]
    else:
        hits = list(spans)

    if not hits:
        hits = list(spans)

    rects = [Rect(page_no=sp.page_no, bbox=[float(v) for v in sp.bbox])
             for sp in hits if sp.page_no is not None and sp.bbox]

    times = [(sp.t_start_ms, sp.t_end_ms) for sp in hits
             if sp.t_start_ms is not None and sp.t_end_ms is not None]
    t_start = min(t[0] for t in times) if times else None
    t_end = max(t[1] for t in times) if times else None

    return _merge_same_line(rects), t_start, t_end


def _merge_same_line(rects: list[Rect]) -> list[Rect]:
    """Join rects that sit on the same text line, so a highlight over one line
    is one rectangle instead of several adjoining ones."""
    if len(rects) < 2:
        return rects
    rects = sorted(rects, key=lambda r: (r.page_no, r.bbox[1], r.bbox[0]))
    out: list[Rect] = [rects[0]]
    for r in rects[1:]:
        prev = out[-1]
        same_page = r.page_no == prev.page_no
        # Vertical overlap of >60% means the same line of text.
        ph, rh = prev.bbox[3] - prev.bbox[1], r.bbox[3] - r.bbox[1]
        overlap = min(prev.bbox[3], r.bbox[3]) - max(prev.bbox[1], r.bbox[1])
        if same_page and min(ph, rh) > 0 and overlap / min(ph, rh) > 0.6:
            prev.bbox = [
                min(prev.bbox[0], r.bbox[0]), min(prev.bbox[1], r.bbox[1]),
                max(prev.bbox[2], r.bbox[2]), max(prev.bbox[3], r.bbox[3]),
            ]
            continue
        out.append(r)
    return out
