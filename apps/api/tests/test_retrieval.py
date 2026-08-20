"""Retrieval helpers that are pure enough to test without a database."""
from __future__ import annotations

from api.app.services.retrieval import Hit, dedupe_adjacent, to_sparsevec


def hit(cid: int, doc: str = "d1") -> Hit:
    return Hit(chunk_id=cid, document_id=doc, filename=f"{doc}.pdf", text=f"t{cid}",
               heading_path=None, page_from=1, page_to=1, t_start_ms=None, t_end_ms=None,
               is_table=False, score=1.0 / cid)


# --------------------------------------------------------------- sparsevec
def test_sparsevec_uses_one_based_indices_and_sorted_order():
    # pgvector's sparsevec text format is 1-based; bge-m3 gives 0-based token ids.
    assert to_sparsevec({5: 0.5, 1: 0.25}, 100) == "{2:0.25,6:0.5}/100"


def test_empty_weights_still_produce_a_valid_sparsevec():
    assert to_sparsevec({}, 250002) == "{}/250002"


def test_sparsevec_survives_numpy_style_string_keys():
    # FlagEmbedding returns lexical weights keyed by token id as a string.
    assert to_sparsevec({"3": 0.75}, 10) == "{4:0.75}/10"


# ------------------------------------------------------------------ dedupe
def test_duplicate_chunks_are_dropped_keeping_first_occurrence():
    out = dedupe_adjacent([hit(1), hit(2), hit(1), hit(3)])
    assert [h.chunk_id for h in out] == [1, 2, 3]


def test_per_document_cap_stops_one_file_crowding_out_the_rest():
    hits = [hit(1, "a"), hit(2, "a"), hit(3, "a"), hit(4, "b"), hit(5, "c")]
    out = dedupe_adjacent(hits, max_per_document=2)
    assert [h.chunk_id for h in out] == [1, 2, 4, 5]


def test_no_cap_keeps_everything_distinct():
    hits = [hit(i, "a") for i in range(1, 6)]
    assert len(dedupe_adjacent(hits)) == 5
