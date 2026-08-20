"""OpenAI-compatible client pointed at the LLM gateway.

The gateway is the only LLM address the application knows. Whether one replica,
one model split across two GPUs, or two load-balanced replicas sit behind it is
a deployment detail (see LLM_MODE) and never leaks into application code.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from api.app.config import settings

log = logging.getLogger("services.llm")

_client = AsyncOpenAI(
    base_url=settings.llm_base_url,
    api_key=settings.llm_api_key,
    timeout=settings.llm_timeout_s,
    max_retries=2,
)


def client() -> AsyncOpenAI:
    return _client


async def complete(messages: list[dict[str, Any]], *, temperature: float = 0.2,
                   max_tokens: int | None = None, **kw: Any) -> str:
    r = await _client.chat.completions.create(
        model=settings.served_model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        **kw,
    )
    return r.choices[0].message.content or ""


async def stream(messages: list[dict[str, Any]], *, temperature: float = 0.2,
                 max_tokens: int | None = None, **kw: Any) -> AsyncIterator[str]:
    resp = await _client.chat.completions.create(
        model=settings.served_model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        **kw,
    )
    async for chunk in resp:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


async def complete_json(messages: list[dict[str, Any]], schema: dict[str, Any],
                        *, temperature: float = 0.0, max_tokens: int = 2048,
                        name: str = "submit",
                        description: str = "결과를 제출합니다.") -> Any:
    """Get a structured decision out of the model.

    Primary path is a **forced tool call**: the schema is presented as a single
    function and ``tool_choice`` pins it. Measured against EXAONE-4.0-32B-AWQ on
    this stack, that beat plain guided JSON decisively — guided decoding produced
    structurally valid but semantically empty objects (``subqueries: [""]``) and
    even the wrong ``intent``, because constraining whitespace pushes the model
    off its natural token path. The tool-call path keeps the model in
    distribution while still guaranteeing the shape.

    ``response_format`` guided decoding remains as the fallback for a served
    model with no usable tool-call parser.
    """
    tools = [{
        "type": "function",
        "function": {"name": name, "description": description, "parameters": schema},
    }]
    try:
        r = await _client.chat.completions.create(
            model=settings.served_model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice={"type": "function", "function": {"name": name}},
        )
        msg = r.choices[0].message
        if msg.tool_calls:
            return _loads_lenient(msg.tool_calls[0].function.arguments)
        # Some models answer in prose despite tool_choice; fall through.
        log.warning("forced tool call produced no tool_calls; falling back to guided JSON")
    except Exception as exc:  # noqa: BLE001
        log.warning("tool-call structured output failed (%s); falling back to guided JSON", exc)

    r = await _client.chat.completions.create(
        model=settings.served_model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": name, "schema": schema, "strict": True},
        },
    )
    return _loads_lenient(r.choices[0].message.content or "{}")


def _loads_lenient(raw: str, *, max_attempts: int = 200) -> Any:
    """Parse JSON, repairing the ways constrained decoding truncates output.

    A grammar that permits free whitespace lets a model stall mid-object until it
    hits the token limit, leaving a prefix of valid JSON. Rather than discard an
    otherwise-usable turn, walk backwards through the positions where the text is
    *not* inside a string, close whatever containers are still open there, and
    take the longest prefix that parses.
    """
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    if start < 0:
        raise ValueError(f"no JSON object in model output: {raw[:200]!r}")
    body = raw[start:]

    end = body.rfind("}")
    if end > 0:
        try:
            return json.loads(body[: end + 1])
        except json.JSONDecodeError:
            pass

    # Record every cut point that sits outside a string literal, together with
    # the containers open at that point.
    cuts: list[tuple[int, tuple[str, ...]]] = []
    stack: list[str] = []
    in_str = escaped = False
    for i, ch in enumerate(body):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
                cuts.append((i + 1, tuple(stack)))
            continue
        if ch == '"':
            in_str = True
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
        cuts.append((i + 1, tuple(stack)))

    for i, open_at_cut in reversed(cuts[-max_attempts:]):
        candidate = body[:i].rstrip().rstrip(",").rstrip()
        # A dangling "key": has no value; drop back further rather than inventing one.
        if not candidate or candidate.endswith(":"):
            continue
        try:
            return json.loads(candidate + "".join(reversed(open_at_cut)))
        except json.JSONDecodeError:
            continue

    raise ValueError(f"unrepairable JSON from model: {raw[:200]!r}")
