"""Table/numeric tool tests — pure functions, no DB/LLM/network."""
from __future__ import annotations

import pytest

from api.app.agent.tools.tables import calc, parse_number, query_table

# 2x3 table: row 0 / col 0 are headers.
#      2분기   3분기
# 매출  1000    1200
TABLE = {
    "n_rows": 2, "n_cols": 3,
    "cells": [
        {"r": 0, "c": 0, "text": ""},
        {"r": 0, "c": 1, "text": "2분기"},
        {"r": 0, "c": 2, "text": "3분기"},
        {"r": 1, "c": 0, "text": "매출"},
        {"r": 1, "c": 1, "text": "1000"},
        {"r": 1, "c": 2, "text": "1200"},
    ],
}


def test_row_and_col_header_returns_intersecting_cell():
    result = query_table(TABLE, row_header="매출", col_header="2분기")
    assert result == [{"r": 1, "c": 1, "text": "1000"}]


def test_row_only_returns_whole_row():
    result = query_table(TABLE, row=1)
    assert {(c["r"], c["c"]) for c in result} == {(1, 0), (1, 1), (1, 2)}


def test_col_header_with_no_match_returns_empty():
    assert query_table(TABLE, col_header="4분기") == []


def test_no_filters_returns_every_cell():
    assert len(query_table(TABLE)) == len(TABLE["cells"])


@pytest.mark.parametrize("text,expected", [
    ("1,234,000원", 1234000.0),
    ("12.5%", 12.5),
    ("42", 42.0),
    ("n/a", None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_calc_basic_arithmetic():
    assert calc("(a - b) / b * 100", {"a": 1200, "b": 1000}) == pytest.approx(20.0)


def test_calc_unknown_name_raises():
    with pytest.raises(ValueError):
        calc("a + b", {"a": 1})


def test_calc_disallowed_node_raises():
    with pytest.raises(ValueError):
        calc("__import__('os').system('echo hi')", {})


def test_calc_division_by_zero_raises():
    with pytest.raises(ValueError):
        calc("a / b", {"a": 1, "b": 0})
