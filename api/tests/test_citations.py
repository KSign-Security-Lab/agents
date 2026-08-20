"""Citation protocol tests.

The streaming parser is the piece most likely to fail invisibly — a marker split
across token boundaries would either leak "[S" into the UI or lose the pill — so
it is tested against adversarial chunkings rather than one happy path.
"""
from __future__ import annotations

import pytest

from api.app.agent.citations import (
    CITE_TOKEN_RE,
    SourceRef,
    StreamingCitationParser,
    build_context_block,
    parse_markers,
    strip_cite_tokens,
    uncited_sentences,
)


def src(sid: int, **kw) -> SourceRef:
    base = dict(
        chunk_id=sid * 10,
        document_id=f"doc-{sid}",
        filename=f"file{sid}.pdf",
        text=f"passage {sid}",
        page_from=sid,
        page_to=sid,
    )
    base.update(kw)
    return SourceRef(sid=sid, **base)


SOURCES = [src(1), src(2), src(3)]


# --------------------------------------------------------------- basic parse
def test_markers_become_pill_tokens_in_order_of_appearance():
    body, cits, rejected = parse_markers("대금은 30일 내 지급한다[S2]. 하자보증은 2년이다[S1].", SOURCES)
    assert body == "대금은 30일 내 지급한다[[cite:1]]. 하자보증은 2년이다[[cite:2]]."
    # Pill numbers are reader-facing and start at 1, independent of the S-number.
    assert [(c.idx, c.source.sid) for c in cits] == [(1, 2), (2, 1)]
    assert rejected == []


def test_repeated_source_reuses_its_pill_number():
    body, cits, _ = parse_markers("가[S1]. 나[S1]. 다[S2].", SOURCES)
    assert body == "가[[cite:1]]. 나[[cite:1]]. 다[[cite:2]]."
    assert len(cits) == 2


def test_unknown_source_id_is_dropped_and_recorded():
    body, cits, rejected = parse_markers("근거 없는 주장[S99]. 실제 근거[S1].", SOURCES)
    assert "[S99]" not in body and "99" not in body
    assert body == "근거 없는 주장. 실제 근거[[cite:1]]."
    assert [c.source.sid for c in cits] == [1]
    assert [(r.sid, r.reason) for r in rejected] == [(99, "unknown_source_id")]


def test_adjacent_markers_produce_two_pills():
    body, cits, _ = parse_markers("두 문서가 일치한다[S1][S2].", SOURCES)
    assert body == "두 문서가 일치한다[[cite:1]][[cite:2]]."
    assert len(cits) == 2


# ----------------------------------------------------------------- streaming
@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 6, 7, 11, 50])
def test_streaming_any_chunk_size_matches_whole_text_parse(size):
    text = "가나다[S2] 라마바[S1]. 사아자[S99] 차카타[S3]!"
    expected, expected_cits, expected_rej = parse_markers(text, SOURCES)

    parser = StreamingCitationParser(SOURCES)
    got = ""
    for i in range(0, len(text), size):
        emitted, _ = parser.feed(text[i : i + size])
        got += emitted
    tail, _ = parser.finish()
    got += tail

    assert got == expected
    assert [(c.idx, c.source.sid) for c in parser.citations] == \
           [(c.idx, c.source.sid) for c in expected_cits]
    assert [r.sid for r in parser.rejected] == [r.sid for r in expected_rej]


def test_marker_split_across_deltas_never_leaks_partial_text():
    parser = StreamingCitationParser(SOURCES)
    shown = ""
    for delta in ["지급조건은 ", "30일", "[S", "1", "]", " 이다."]:
        emitted, _ = parser.feed(delta)
        # At no point may a half-written marker be shown to the reader.
        assert "[S" not in emitted
        shown += emitted
    tail, _ = parser.finish()
    shown += tail
    assert shown == "지급조건은 30일[[cite:1]] 이다."


def test_citation_is_emitted_as_soon_as_its_marker_closes():
    parser = StreamingCitationParser(SOURCES)
    _, new = parser.feed("주장입니다[S3")
    assert new == []                      # marker not closed yet
    _, new = parser.feed("]")
    assert [c.source.sid for c in new] == [3]


def test_dangling_bracket_is_emitted_verbatim_at_end():
    parser = StreamingCitationParser(SOURCES)
    shown, _ = parser.feed("배열 표기 a[S")
    tail, _ = parser.finish()
    assert shown + tail == "배열 표기 a[S"


def test_text_that_merely_looks_like_a_marker_is_left_alone():
    body, cits, _ = parse_markers("코드에서 arr[S] 와 [SS1] 은 마커가 아니다.", SOURCES)
    assert body == "코드에서 arr[S] 와 [SS1] 은 마커가 아니다."
    assert cits == []


# ------------------------------------------------------------------ prompting
def test_context_block_labels_pages_and_headings():
    block = build_context_block([
        src(1, filename="2026_계약서.pdf", page_from=14, page_to=14,
            heading_path="3. 계약조건 > 3.2 지급조건", text="대금은 검수 완료 후 30일 내 지급한다."),
    ])
    assert block.startswith("[S1] (2026_계약서.pdf · p.14 · 3. 계약조건 > 3.2 지급조건)")
    assert "30일" in block


def test_context_block_labels_a_recording_with_a_timestamp():
    block = build_context_block([
        src(1, filename="회의록.m4a", page_from=None, page_to=None,
            t_start_ms=3_725_000, text="지급 일정은 다음 분기로 미룹니다."),
    ])
    assert "회의록.m4a · 1:02:05" in block


def test_long_passages_are_truncated_not_dropped():
    block = build_context_block([src(1, text="가" * 5000)], max_chars_per_source=100)
    assert "…" in block and len(block) < 400


# --------------------------------------------------------------- verify node
def test_uncited_sentences_flags_only_real_claims():
    content = (
        "대금은 30일 내 지급합니다[[cite:1]]. "
        "하자보증 기간은 명시되어 있지 않습니다. "
        "## 요약\n"
        "- 항목 하나\n"
        "무엇을 더 확인해 드릴까요?"
    )
    flagged = uncited_sentences(content)
    assert flagged == ["하자보증 기간은 명시되어 있지 않습니다."]


def test_strip_cite_tokens_leaves_clean_prose():
    assert strip_cite_tokens("가[[cite:1]] 나[[cite:12]].") == "가 나."
    assert CITE_TOKEN_RE.findall("가[[cite:1]] 나[[cite:12]].") == ["1", "12"]


# ------------------------------------------------- multi-turn contamination
def test_stripping_prevents_the_model_imitating_our_internal_token_syntax():
    """Regression: stored answers carry [[cite:N]] tokens.

    Feeding those back as conversation history made the model copy the syntax and
    emit "[[cite:S1]]" on the next turn, which the marker parser does not
    recognise — so every follow-up answer silently lost all of its citations.
    """
    stored = "하자보증 기간은 2년입니다[[cite:1]]. 지급은 30일 이내입니다[[cite:2]]."
    clean = strip_cite_tokens(stored)
    assert "[[cite:" not in clean
    assert clean == "하자보증 기간은 2년입니다. 지급은 30일 이내입니다."


def test_a_model_that_copies_the_token_syntax_yields_no_false_pills():
    # If contamination happens anyway, the parser must not invent a citation.
    body, cits, rejected = parse_markers("주장입니다[[cite:S1]].", SOURCES)
    assert cits == []
    assert "[[cite:" in body            # left as literal text, not a pill
    assert rejected == []


def test_stripper_also_removes_malformed_variants():
    """A polluted transcript must not keep re-teaching the bad syntax.

    Once a turn has stored "[[cite:S1]]", a digits-only stripper leaves it in the
    history and every later turn imitates it again.
    """
    from api.app.agent.citations import strip_cite_tokens as strip

    assert strip("가[[cite:S1]] 나[[cite:1]] 다[[ cite : 12 ]].") == "가 나 다."
    # Ordinary bracketed text is untouched.
    assert strip("배열 a[[1]] 과 [[cite]] 은 그대로") == "배열 a[[1]] 과 [[cite]] 은 그대로"
