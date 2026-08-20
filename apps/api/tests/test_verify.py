"""Verify-pass tests.

``apply_fixes`` reuses the live compose-time parser, so the main risk is idx
numbering drifting between a compose-time citation and a verify-time one, or
a fix silently corrupting the answer when the model doesn't echo the flagged
sentence verbatim.
"""
from __future__ import annotations

from api.app.agent.citations import SourceRef, StreamingCitationParser
from api.app.agent.nodes.verify import apply_fixes


def src(sid: int, **kw) -> SourceRef:
    base = dict(chunk_id=sid * 10, document_id=f"doc-{sid}", filename=f"file{sid}.pdf",
                text=f"passage {sid}")
    base.update(kw)
    return SourceRef(sid=sid, **base)


SOURCES = [src(1), src(2), src(3)]


def test_hedge_fix_replaces_sentence_without_adding_citation():
    parser = StreamingCitationParser(SOURCES)
    answer = "가격은 명시되어 있습니다. 하자보증 기간은 알 수 없습니다."
    fixes = [{"sentence": "하자보증 기간은 알 수 없습니다.", "action": "hedge",
              "source_ids": [], "replacement": "하자보증 기간은 문서에서 확인되지 않습니다."}]

    text, new_cits, fixed = apply_fixes(answer, fixes, {1, 2, 3}, parser)

    assert text == "가격은 명시되어 있습니다. 하자보증 기간은 문서에서 확인되지 않습니다."
    assert new_cits == []
    assert fixed == 1


def test_cite_fix_continues_idx_numbering_from_existing_parser_state():
    parser = StreamingCitationParser(SOURCES)
    # Compose already cited source 1 as pill 1.
    parser.feed("가격은 1억이다[S1].")
    parser.finish()
    assert [c.idx for c in parser.citations] == [1]

    answer = "가격은 1억이다[[cite:1]]. 하자보증 기간은 알 수 없습니다."
    fixes = [{"sentence": "하자보증 기간은 알 수 없습니다.", "action": "cite",
              "source_ids": [2], "replacement": "하자보증 기간은 2년이다."}]

    text, new_cits, fixed = apply_fixes(answer, fixes, {1, 2, 3}, parser)

    assert fixed == 1
    assert [c.idx for c in new_cits] == [2]
    assert "[[cite:2]]" in text
    assert [c.idx for c in parser.citations] == [1, 2]


def test_cite_fix_with_unknown_source_id_is_dropped():
    parser = StreamingCitationParser(SOURCES)
    answer = "하자보증 기간은 알 수 없습니다."
    fixes = [{"sentence": "하자보증 기간은 알 수 없습니다.", "action": "cite",
              "source_ids": [99], "replacement": "하자보증 기간은 2년이다."}]

    text, new_cits, fixed = apply_fixes(answer, fixes, {1, 2, 3}, parser)

    assert text == answer
    assert new_cits == []
    assert fixed == 0


def test_keep_action_leaves_sentence_unchanged():
    parser = StreamingCitationParser(SOURCES)
    answer = "하자보증 기간은 알 수 없습니다."
    fixes = [{"sentence": "하자보증 기간은 알 수 없습니다.", "action": "keep",
              "source_ids": [], "replacement": ""}]

    text, new_cits, fixed = apply_fixes(answer, fixes, {1, 2, 3}, parser)

    assert text == answer
    assert new_cits == []
    assert fixed == 0


def test_sentence_not_found_in_answer_is_skipped_safely():
    parser = StreamingCitationParser(SOURCES)
    answer = "하자보증 기간은 알 수 없습니다."
    fixes = [{"sentence": "이 문장은 답변에 없다.", "action": "hedge",
              "source_ids": [], "replacement": "무언가"}]

    text, new_cits, fixed = apply_fixes(answer, fixes, {1, 2, 3}, parser)

    assert text == answer
    assert new_cits == []
    assert fixed == 0
