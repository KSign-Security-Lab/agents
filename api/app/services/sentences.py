"""Sentence segmentation for Korean and English.

Deliberately dependency-free. It is used in two places — narrowing a citation to
the sentences that actually supported a claim, and windowing transcripts — and in
both, a slightly wide split only widens a highlight a little, whereas a heavy NLP
dependency would put torch in the API image and make ingest-time and answer-time
splitting disagree.
"""
from __future__ import annotations

import re

# A period preceded by any of these is an abbreviation or a decimal, not a
# sentence break. Anchored on a word boundary so "30 days." is not mistaken for
# a 3-letter abbreviation.
_NO_BREAK_BEFORE = re.compile(
    r"(?:^|\s|\()(?:[A-Za-z]{1,3}|No|Fig|Tbl|vs|etc|cf|e\.g|i\.e|Mr|Ms|Mrs|Dr|Prof|approx)$"
)
# A digit before the period means a decimal or a numbered clause such as "3.2".
_DIGIT_BEFORE = re.compile(r"\d$")

# Sentence boundaries: terminal punctuation (with any closing quotes/brackets)
# followed by whitespace, or a hard line break. Korean formal writing terminates
# sentences with a period, so no Korean-specific ending rule is needed — and a
# rule that split on a bare "다" would wreck phrases like "그 사람이 다 왔다".
_BOUNDARY = re.compile(r'(?:(?<=[.!?\u2026])["\'\u201d\u2019\)\]]*\s+|\n+)')


def _is_hard_break(text: str, start: int, end: int) -> bool:
    """False when the punctuation that triggered this boundary is really an
    abbreviation dot or a decimal point."""
    i = end - 1
    while i >= start and text[i] not in ".!?\u2026":
        i -= 1
    if i < start or text[i] != ".":
        return True
    head = text[start:i]
    return not (_NO_BREAK_BEFORE.search(head) or _DIGIT_BEFORE.search(head))


def split_with_offsets(text: str, *, min_len: int = 2) -> list[tuple[int, int, str]]:
    """Split ``text`` into ``(start, end, sentence)`` triples.

    Offsets index into the original string, which is what lets a matched
    sentence be mapped back to page geometry via ``chunk_spans``.
    """
    if not text:
        return []

    out: list[tuple[int, int, str]] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        end = m.start()
        if not _is_hard_break(text, start, end):
            continue
        piece = text[start:end]
        if piece.strip():
            out.append((start, end, piece))
        start = m.end()

    if start < len(text) and text[start:].strip():
        out.append((start, len(text), text[start:]))

    # Merge fragments too short to be meaningful into the previous sentence, so
    # a stray "(1)" does not become its own citation target.
    merged: list[tuple[int, int, str]] = []
    for s, e, piece in out:
        if merged and len(piece.strip()) < min_len:
            ps, _, ptext = merged[-1]
            merged[-1] = (ps, e, text[ps:e])
            continue
        merged.append((s, e, piece))
    return merged


def split(text: str) -> list[str]:
    return [t.strip() for _, _, t in split_with_offsets(text) if t.strip()]


def sentence_containing(text: str, position: int) -> tuple[int, int, str] | None:
    """The sentence that spans ``position`` — used to recover the claim a
    citation marker was attached to."""
    for s, e, piece in split_with_offsets(text):
        if s <= position <= e:
            return s, e, piece
    return None
