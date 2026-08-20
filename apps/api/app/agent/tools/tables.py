"""Pure operations over a ``table_json``-shaped dict.

No DB, no LLM, no network — these are exercised directly by unit tests. The
async layer that resolves a chunk to its ``table_json`` and drives the
tool-calling loop lives in ``api.app.agent.nodes.tables``.
"""
from __future__ import annotations

import ast
import operator
import re

# Convention followed by the tables this system ingests: row 0 holds column
# headers, column 0 holds row headers.
_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def query_table(
    table_json: dict,
    *,
    row: int | None = None,
    col: int | None = None,
    row_header: str | None = None,
    col_header: str | None = None,
) -> list[dict]:
    """Select cells from ``table_json`` (``{"n_rows","n_cols","cells":[...]}``).

    ``row``/``col`` select by exact index when known. ``row_header``/
    ``col_header`` match by substring against the header cells in column 0 /
    row 0. Both axes given -> the intersection cell(s); one axis -> the whole
    row or column; neither -> every cell. Returns
    ``[{"r": int, "c": int, "text": str}, ...]`` — bbox is intentionally
    omitted, since a tool-derived number is cited back to the whole table
    chunk, the same as any other table citation.
    """
    cells = table_json.get("cells", [])

    if row_header is not None:
        rows = {c["r"] for c in cells if c.get("c") == 0 and row_header in (c.get("text") or "")}
    else:
        rows = None

    if col_header is not None:
        cols = {c["c"] for c in cells if c.get("r") == 0 and col_header in (c.get("text") or "")}
    else:
        cols = None

    def matches(c: dict) -> bool:
        if row is not None and c.get("r") != row:
            return False
        if col is not None and c.get("c") != col:
            return False
        if rows is not None and c.get("r") not in rows:
            return False
        if cols is not None and c.get("c") not in cols:
            return False
        return True

    return [{"r": c["r"], "c": c["c"], "text": c.get("text", "")} for c in cells if matches(c)]


def parse_number(text: str) -> float | None:
    """``"1,234,000원"`` -> ``1234000.0``, ``"12.5%"`` -> ``12.5``, else ``None``.

    A trailing ``%`` is reported at face value, not divided by 100 — callers
    that want a ratio divide explicitly, keeping this function's contract
    unambiguous.
    """
    m = _NUMBER_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARYOPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}


def calc(expression: str, values: dict[str, float]) -> float:
    """Evaluate ``expression`` (+ - * / ** parens, unary -, names from
    ``values``) via a restricted AST walk — never ``eval()``.

    Raises ``ValueError`` on any disallowed node, unknown name, or division by
    zero, so a malformed or adversarial expression fails closed.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid expression: {expression!r}") from exc

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise ValueError(f"unknown name: {node.id!r}")
            return float(values[node.id])
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ValueError("division by zero")
            return _ALLOWED_BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
            return _ALLOWED_UNARYOPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"disallowed expression node: {type(node).__name__}")

    return _eval(tree)
