"""Citation marker protocol.

The model is never trusted to produce references. Instead:

1. Retrieval hands the model a numbered list of passages, ``[S1] .. [Sn]``.
2. The model is instructed to write ``[S3]`` inline right after any claim it
   took from passage 3.
3. This module parses those markers *out of the stream as it arrives*, discards
   any id that was not actually offered, renumbers the survivors into the
   compact pill numbers the reader sees, and rewrites them as ``[[cite:1]]``
   tokens in the stored content.

Everything here is pure so the protocol can be tested without a GPU, a database
or a model. Geometry resolution lives in ``resolve.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# What the model writes.
MARKER_RE = re.compile(r"\[S(\d{1,3})\]")
# A trailing fragment that might still grow into a marker, e.g. "[", "[S", "[S1".
PARTIAL_RE = re.compile(r"\[S?\d{0,3}$")
# The longest marker is "[S999]".
MAX_MARKER_LEN = 6

# What we store, and what the web client splits on to place pills.
CITE_TOKEN = "[[cite:{idx}]]"
CITE_TOKEN_RE = re.compile(r"\[\[cite:(\d{1,3})\]\]")
# Deliberately looser than CITE_TOKEN_RE: used when cleaning text for the model.
# A model shown "[[cite:1]]" in its own history will imitate the syntax and emit
# variants like "[[cite:S1]]", which are not valid pills but would go on
# teaching the next turn if they survived stripping.
ANY_CITE_TOKEN_RE = re.compile(r"\[\[\s*cite\s*:[^\]]{0,16}\]\]", re.IGNORECASE)


@dataclass(slots=True)
class SourceRef:
    """A retrieved passage offered to the model under a given ``sid``."""

    sid: int
    chunk_id: int
    document_id: str
    filename: str
    text: str
    heading_path: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    t_start_ms: int | None = None
    t_end_ms: int | None = None
    score: float | None = None
    is_table: bool = False
    # True when the passage came from outside the session's selected documents.
    out_of_scope: bool = False

    def label(self) -> str:
        """The human-readable locator shown to the model and in the tooltip."""
        parts = [self.filename]
        if self.page_from:
            parts.append(f"p.{self.page_from}" if self.page_from == (self.page_to or self.page_from)
                         else f"pp.{self.page_from}-{self.page_to}")
        elif self.t_start_ms is not None:
            parts.append(_hhmmss(self.t_start_ms))
        if self.heading_path:
            parts.append(self.heading_path)
        return " · ".join(parts)


@dataclass(slots=True)
class Citation:
    """A marker that survived validation, in reader-facing numbering."""

    idx: int
    source: SourceRef


@dataclass(slots=True)
class RejectedMarker:
    sid: int
    reason: str


def _hhmmss(ms: int) -> str:
    total = ms // 1000
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


class StreamingCitationParser:
    """Converts a token stream containing ``[Sn]`` into display text plus
    citations, emitting each citation the moment its marker completes.

    Token boundaries fall wherever the tokenizer likes, so a marker routinely
    arrives split across two deltas ("[S" then "12]"). Any tail that could still
    grow into a marker is held back rather than emitted, which is what stops a
    literal "[S" from flashing in the UI.
    """

    def __init__(self, sources: Iterable[SourceRef]) -> None:
        self._by_sid: dict[int, SourceRef] = {s.sid: s for s in sources}
        self._buf = ""
        self._idx_for_sid: dict[int, int] = {}
        self.citations: list[Citation] = []
        self.rejected: list[RejectedMarker] = []

    # ------------------------------------------------------------------ api
    def feed(self, delta: str) -> tuple[str, list[Citation]]:
        """Consume a streamed delta. Returns text safe to display now, plus any
        citations that became known during this delta."""
        self._buf += delta
        return self._drain(final=False)

    def finish(self) -> tuple[str, list[Citation]]:
        """Flush the buffer at end of stream. A dangling partial marker is
        emitted verbatim — it was never a marker."""
        return self._drain(final=True)

    # -------------------------------------------------------------- internal
    def _drain(self, *, final: bool) -> tuple[str, list[Citation]]:
        out: list[str] = []
        new: list[Citation] = []

        while True:
            m = MARKER_RE.search(self._buf)
            if not m:
                break
            out.append(self._buf[: m.start()])
            sid = int(m.group(1))
            token = self._accept(sid, new)
            if token:
                out.append(token)
            self._buf = self._buf[m.end() :]

        if final:
            out.append(self._buf)
            self._buf = ""
        else:
            # Hold back only a tail that could still become a marker.
            keep = 0
            tail = self._buf[-(MAX_MARKER_LEN - 1) :]
            pm = PARTIAL_RE.search(tail)
            if pm:
                keep = len(tail) - pm.start()
            if keep:
                out.append(self._buf[: len(self._buf) - keep])
                self._buf = self._buf[len(self._buf) - keep :]
            else:
                out.append(self._buf)
                self._buf = ""

        return "".join(out), new

    def _accept(self, sid: int, new: list[Citation]) -> str:
        """Validate one marker and return the token to write in its place."""
        src = self._by_sid.get(sid)
        if src is None:
            # A hallucinated reference. Drop it silently from the prose and keep
            # it for the run record, so citation drift is measurable.
            self.rejected.append(RejectedMarker(sid=sid, reason="unknown_source_id"))
            return ""

        idx = self._idx_for_sid.get(sid)
        if idx is None:
            idx = len(self._idx_for_sid) + 1
            self._idx_for_sid[sid] = idx
            cit = Citation(idx=idx, source=src)
            self.citations.append(cit)
            new.append(cit)
        return CITE_TOKEN.format(idx=idx)


def parse_markers(text: str, sources: Iterable[SourceRef]) -> tuple[str, list[Citation], list[RejectedMarker]]:
    """Non-streaming convenience wrapper with identical semantics."""
    parser = StreamingCitationParser(sources)
    body, _ = parser.feed(text)
    tail, _ = parser.finish()
    return body + tail, parser.citations, parser.rejected


def build_context_block(sources: Iterable[SourceRef], *, max_chars_per_source: int = 2400) -> str:
    """Render retrieved passages for the prompt.

    The locator goes on the same line as the id so the model can be asked to
    cite precisely without being shown raw ids it might copy incorrectly.
    """
    blocks: list[str] = []
    for s in sources:
        body = s.text.strip()
        if len(body) > max_chars_per_source:
            body = body[:max_chars_per_source].rstrip() + " …"
        blocks.append(f"[S{s.sid}] ({s.label()})\n{body}")
    return "\n\n".join(blocks)


def uncited_sentences(content: str) -> list[str]:
    """Sentences that assert something but carry no citation token.

    Used by the verify node: an answer sentence with no source is either given
    one on a second pass or hedged. Deliberately conservative — questions,
    headers, list scaffolding and very short fragments are ignored.
    """
    out: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", content):
        s = raw.strip()
        if len(s) < 12:
            continue
        if s.startswith(("#", "|", "```", "- ", "* ", ">")):
            continue
        if s.endswith("?") or s.endswith("니까?") or s.endswith("까?"):
            continue
        if CITE_TOKEN_RE.search(s):
            continue
        out.append(s)
    return out


def strip_cite_tokens(content: str) -> str:
    """Remove pill tokens, including malformed variants.

    Used for embedding comparisons, eval scoring, and — most importantly —
    cleaning assistant history before it is shown to the model again.
    """
    return ANY_CITE_TOKEN_RE.sub("", content)
