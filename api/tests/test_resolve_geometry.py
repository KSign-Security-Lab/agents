"""Geometry tests for citation highlighting.

"The highlight is 20px off" is the failure mode that silently ruins this
feature, so the offset->rect mapping is pinned down here before any browser is
involved.
"""
from __future__ import annotations

from dataclasses import dataclass

from api.app.agent.resolve import Rect, _geometry, _merge_same_line, claim_for_citation


@dataclass
class FakeSpan:
    """Stand-in for ChunkSpan: only the fields _geometry reads."""

    text_start: int
    text_end: int
    page_no: int | None = None
    bbox: list[float] | None = None
    t_start_ms: int | None = None
    t_end_ms: int | None = None


# Chunk text: three lines, each its own span.
#   0..20  "대금은 30일 내 지급한다."   line 1 on page 14
#  21..44  "하자보증은 2년이다."        line 2 on page 14
#  45..70  "해지는 3.2 조항을 따른다."  line 3 on page 15
SPANS = [
    FakeSpan(0, 20, 14, [72.0, 700.0, 300.0, 714.0]),
    FakeSpan(21, 44, 14, [72.0, 682.0, 260.0, 696.0]),
    FakeSpan(45, 70, 15, [72.0, 700.0, 280.0, 714.0]),
]


def test_only_spans_overlapping_the_picked_sentence_are_highlighted():
    picked = [(21, 44, "하자보증은 2년이다.")]
    rects, t0, t1 = _geometry(SPANS, picked)
    assert [r.page_no for r in rects] == [14]
    assert rects[0].bbox == [72.0, 682.0, 260.0, 696.0]
    assert (t0, t1) == (None, None)


def test_a_sentence_spanning_a_page_break_yields_a_rect_on_each_page():
    picked = [(30, 60, "…crosses the page boundary…")]
    rects, _, _ = _geometry(SPANS, picked)
    assert sorted(r.page_no for r in rects) == [14, 15]


def test_no_picked_sentences_falls_back_to_the_whole_chunk():
    rects, _, _ = _geometry(SPANS, [])
    assert len(rects) == 3


def test_picked_sentence_matching_nothing_still_produces_a_highlight():
    # A citation with no highlight is worse than an imprecise one.
    rects, _, _ = _geometry(SPANS, [(500, 600, "out of range")])
    assert len(rects) == 3


def test_no_spans_means_no_rects_rather_than_a_crash():
    assert _geometry([], [(0, 10, "x")]) == ([], None, None)


def test_transcript_spans_resolve_to_a_time_range_not_rects():
    spans = [
        FakeSpan(0, 30, None, None, t_start_ms=61_000, t_end_ms=67_500),
        FakeSpan(31, 60, None, None, t_start_ms=67_500, t_end_ms=74_000),
    ]
    rects, t0, t1 = _geometry(spans, [(31, 60, "두 번째 발화")])
    assert rects == []
    assert (t0, t1) == (67_500, 74_000)


# ------------------------------------------------------------- line merging
def test_rects_on_the_same_line_merge_into_one():
    merged = _merge_same_line([
        Rect(14, [72.0, 700.0, 150.0, 714.0]),
        Rect(14, [152.0, 701.0, 300.0, 713.0]),   # same line, vertically overlapping
    ])
    assert len(merged) == 1
    assert merged[0].bbox == [72.0, 700.0, 300.0, 714.0]


def test_rects_on_different_lines_stay_separate():
    merged = _merge_same_line([
        Rect(14, [72.0, 700.0, 300.0, 714.0]),
        Rect(14, [72.0, 682.0, 260.0, 696.0]),    # next line down, no overlap
    ])
    assert len(merged) == 2


def test_same_coordinates_on_different_pages_never_merge():
    merged = _merge_same_line([
        Rect(14, [72.0, 700.0, 300.0, 714.0]),
        Rect(15, [72.0, 700.0, 300.0, 714.0]),
    ])
    assert len(merged) == 2


def test_merged_rects_are_ordered_top_to_bottom_by_page():
    merged = _merge_same_line([
        Rect(15, [72.0, 700.0, 200.0, 714.0]),
        Rect(14, [72.0, 500.0, 200.0, 514.0]),
        Rect(14, [72.0, 700.0, 200.0, 714.0]),
    ])
    assert [(r.page_no, r.bbox[1]) for r in merged] == [(14, 500.0), (14, 700.0), (15, 700.0)]


# ------------------------------------------------------------- claim recovery
def test_claim_recovers_the_sentence_carrying_the_pill():
    content = ("하자보증 기간은 2년입니다[[cite:1]]. "
               "대금은 검수 후 30일 내 지급합니다[[cite:2]].")
    assert claim_for_citation(content, 1) == "하자보증 기간은 2년입니다."
    assert claim_for_citation(content, 2) == "대금은 검수 후 30일 내 지급합니다."


def test_claim_falls_back_to_the_whole_answer_when_the_pill_is_absent():
    content = "근거를 찾지 못했습니다."
    assert claim_for_citation(content, 7) == content
