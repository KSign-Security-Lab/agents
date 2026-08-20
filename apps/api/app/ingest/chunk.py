"""Turn extracted elements into retrieval units that remember where they came from.

Two things make this more than a text splitter:

* **Heading path.** Each chunk records the section it sits under
  ("3. 계약조건 > 3.2 지급조건"), which goes into the prompt so the model can cite a
  location a person can find, and into the citation tooltip.
* **Span carry-through.** Element spans are re-based onto chunk-local offsets, so
  a chunk still knows which page rectangle every character of its text came from.
  Losing this here would cap citation precision at "somewhere in this chunk"
  no matter what the answer-time logic does.

Tables become their own chunk: splitting a table across chunks would leave rows
without their header, and the model could no longer say which cell a figure came
from.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from api.app.config import settings
from api.app.db.models import ElementKind
from api.app.ingest.extract import Element, Span
from api.app.services import sentences as sent

# bge-m3 is byte-pair based; for mixed Korean/English text ~2 characters per
# token is a good approximation, and being slightly conservative only costs a
# little context.
CHARS_PER_TOKEN = 2.0


@dataclass(slots=True)
class Chunk:
    ord: int
    text: str
    heading_path: str | None
    page_from: int | None
    page_to: int | None
    spans: list[Span] = field(default_factory=list)
    is_table: bool = False
    element_index: int | None = None
    t_start_ms: int | None = None
    t_end_ms: int | None = None

    @property
    def token_estimate(self) -> int:
        return max(1, int(len(self.text) / CHARS_PER_TOKEN))


def target_chars() -> int:
    return int(settings.chunk_target_tokens * CHARS_PER_TOKEN)


def overlap_chars() -> int:
    return int(settings.chunk_overlap_tokens * CHARS_PER_TOKEN)


def chunk_elements(elements: list[Element]) -> list[Chunk]:
    """Group elements into token-bounded chunks, respecting section boundaries."""
    limit = target_chars()
    overlap = overlap_chars()

    chunks: list[Chunk] = []
    heading_stack: list[tuple[int, str]] = []   # (level, text)
    buf: list[tuple[Element, int]] = []         # (element, index)
    buf_len = 0

    def heading_path() -> str | None:
        return " > ".join(t for _, t in heading_stack) if heading_stack else None

    def flush() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        chunks.append(_assemble([e for e, _ in buf], len(chunks), heading_path()))
        # Carry the tail of this chunk into the next so a fact split across the
        # boundary is still retrievable from at least one side.
        if overlap > 0 and len(buf) > 1:
            tail: list[tuple[Element, int]] = []
            acc = 0
            for item in reversed(buf):
                acc += len(item[0].text)
                tail.insert(0, item)
                if acc >= overlap:
                    break
            buf = tail if len(tail) < len(buf) else []
            buf_len = sum(len(e.text) for e, _ in buf)
        else:
            buf, buf_len = [], 0

    for idx, el in enumerate(elements):
        if el.kind == ElementKind.heading:
            # A new section starts a new chunk: mixing two sections dilutes the
            # embedding and makes the heading path ambiguous.
            flush()
            buf, buf_len = [], 0
            level = el.level or 3
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, el.text.strip()))
            buf.append((el, idx))
            buf_len += len(el.text)
            continue

        if el.kind == ElementKind.table:
            flush()
            buf, buf_len = [], 0
            chunks.append(_assemble([el], len(chunks), heading_path(), is_table=True,
                                    element_index=idx))
            continue

        if buf_len + len(el.text) > limit and buf:
            flush()

        # A single element larger than the budget is split on sentence boundaries.
        if len(el.text) > limit:
            flush()
            buf, buf_len = [], 0
            for piece in _split_element(el, limit):
                chunks.append(_assemble([piece], len(chunks), heading_path()))
            continue

        buf.append((el, idx))
        buf_len += len(el.text)

    flush()
    return [c for c in chunks if c.text.strip()]


def _assemble(elements: list[Element], ord_: int, heading_path: str | None, *,
              is_table: bool = False, element_index: int | None = None) -> Chunk:
    """Join elements and rebase their spans onto the chunk's own offsets."""
    parts: list[str] = []
    spans: list[Span] = []
    cursor = 0
    for el in elements:
        text = el.text
        for sp in el.spans:
            spans.append(Span(
                text_start=cursor + sp.text_start,
                text_end=cursor + sp.text_end,
                page_no=sp.page_no,
                bbox=sp.bbox,
            ))
        if not el.spans and el.bbox:
            # No line-level detail (an OCR element): one span for the whole thing.
            spans.append(Span(text_start=cursor, text_end=cursor + len(text),
                              page_no=el.page_no, bbox=el.bbox))
        parts.append(text)
        cursor += len(text) + 1

    pages = [el.page_no for el in elements if el.page_no]
    return Chunk(
        ord=ord_,
        text="\n".join(parts),
        heading_path=heading_path,
        page_from=min(pages) if pages else None,
        page_to=max(pages) if pages else None,
        spans=spans,
        is_table=is_table,
        element_index=element_index,
    )


def _split_element(el: Element, limit: int) -> list[Element]:
    """Split an oversized element on sentence boundaries, keeping span geometry
    aligned with the new, shorter texts."""
    pieces: list[Element] = []
    sentences = sent.split_with_offsets(el.text)
    if not sentences:
        return [el]

    start = sentences[0][0]
    end = start
    for s_start, s_end, _ in sentences:
        if s_end - start > limit and end > start:
            pieces.append(_slice_element(el, start, end))
            start = s_start
        end = s_end
    if end > start:
        pieces.append(_slice_element(el, start, end))
    return pieces or [el]


def _slice_element(el: Element, start: int, end: int) -> Element:
    """A sub-range of an element, with spans clipped and re-based."""
    spans: list[Span] = []
    for sp in el.spans:
        if sp.text_end <= start or sp.text_start >= end:
            continue
        spans.append(Span(
            text_start=max(0, sp.text_start - start),
            text_end=min(end, sp.text_end) - start,
            page_no=sp.page_no,
            bbox=sp.bbox,
        ))
    return Element(
        kind=el.kind, page_no=el.page_no, reading_order=el.reading_order,
        text=el.text[start:end], bbox=el.bbox, level=el.level, spans=spans,
    )


# ------------------------------------------------------- transcripts ---------
def chunk_transcript(segments: list[dict], *, window_s: int | None = None) -> list[Chunk]:
    """Window a transcript into chunks on sentence boundaries.

    Timestamps replace page geometry here: a citation into a recording is a time
    range, and the player seeks to it. Windows end on a sentence where possible
    so a quoted claim is not cut mid-clause.
    """
    window_ms = (window_s or settings.transcript_window_s) * 1000
    chunks: list[Chunk] = []
    buf: list[dict] = []

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        parts: list[str] = []
        spans: list[Span] = []
        cursor = 0
        for seg in buf:
            text = seg["text"].strip()
            spans.append(Span(text_start=cursor, text_end=cursor + len(text),
                              t_start_ms=seg["start_ms"], t_end_ms=seg["end_ms"]))
            parts.append(text)
            cursor += len(text) + 1
        chunks.append(Chunk(
            ord=len(chunks),
            text=" ".join(parts),
            heading_path=None,
            page_from=None,
            page_to=None,
            spans=spans,
            t_start_ms=buf[0]["start_ms"],
            t_end_ms=buf[-1]["end_ms"],
        ))
        buf = []

    for seg in segments:
        if not seg.get("text", "").strip():
            continue
        if buf and (seg["end_ms"] - buf[0]["start_ms"]) > window_ms:
            # Prefer to close a window at a sentence end so a quoted claim is not
            # cut mid-clause; give up on that after 1.6x the target window.
            ends_sentence = buf[-1]["text"].strip().endswith((".", "!", "?", "\u2026", "다", "요"))
            if ends_sentence or (seg["end_ms"] - buf[0]["start_ms"]) > window_ms * 1.6:
                flush()
        buf.append(seg)
    flush()
    return chunks
