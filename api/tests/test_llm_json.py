"""Repair of truncated structured output.

Constrained decoding on this stack can stall mid-object and hit the token limit,
leaving a valid JSON *prefix*. These cases pin down that a usable object is
recovered instead of the whole turn being thrown away.
"""
from __future__ import annotations

import pytest

from api.app.services.llm_client import _loads_lenient


@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ('  {"a": 1}  ', {"a": 1}),
    ('here you go {"a": 1} thanks', {"a": 1}),
])
def test_wellformed_and_surrounded_json(raw, expected):
    assert _loads_lenient(raw) == expected


def test_stall_after_an_array_is_closed_up():
    # The exact shape observed from the model: object left open after the array,
    # followed by a run of whitespace until the token limit.
    raw = '{\n "intent": "compare",\n "subqueries": ["가","나"]\n   \n   \n'
    assert _loads_lenient(raw) == {"intent": "compare", "subqueries": ["가", "나"]}


def test_truncation_inside_a_nested_array():
    assert _loads_lenient('{"topics": ["계약", "법무"') == {"topics": ["계약", "법무"]}


def test_truncation_mid_string_drops_the_partial_value():
    # "summary" was cut mid-word; the key is dropped rather than half-invented.
    assert _loads_lenient('{"topics": ["계약"], "summary": "이 문서는 계') == {"topics": ["계약"]}


def test_dangling_key_with_no_value_is_dropped():
    assert _loads_lenient('{"a": 1, "b":') == {"a": 1}


def test_trailing_comma_before_truncation():
    assert _loads_lenient('{"a": [1,2],') == {"a": [1, 2]}


def test_deeply_nested_truncation():
    assert _loads_lenient('{"a": {"b": [1, {"c": "x"') == {"a": {"b": [1, {"c": "x"}]}}


def test_braces_inside_strings_are_not_treated_as_structure():
    assert _loads_lenient('{"s": "brace } inside string"}') == {"s": "brace } inside string"}


def test_escaped_quotes_do_not_confuse_the_scanner():
    assert _loads_lenient('{"s": "escaped \\" quote", "t": 2') == {"s": 'escaped " quote', "t": 2}


def test_garbage_raises_rather_than_returning_nonsense():
    with pytest.raises(ValueError):
        _loads_lenient("no json at all")
