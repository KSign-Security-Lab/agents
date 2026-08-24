"""The transcribe hop must not depend on a shared filesystem.

infer used to read the recording off a /storage mount it shared with the worker,
which silently 404s the moment the two are on different machines — a second dev
machine, or two nodes of a cluster. These pin the upload contract instead.
"""
from __future__ import annotations

import httpx
import pytest

from api.app.services.infer_client import InferClient


def _client(handler) -> InferClient:
    c = InferClient(base_url="http://infer.test")
    c._client = httpx.AsyncClient(base_url="http://infer.test",
                                  transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_sends_the_bytes_not_a_path(tmp_path):
    wav = tmp_path / "meeting.16k.wav"
    wav.write_bytes(b"RIFF....fake wav payload")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["type"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        return httpx.Response(200, json={"language": "ko", "duration_ms": 1000,
                                         "segments": [], "text": ""})

    out = await _client(handler).transcribe(wav)
    assert out["language"] == "ko"
    assert seen["url"].endswith("/transcribe")
    # multipart, carrying the file's actual content and name
    assert seen["type"].startswith("multipart/form-data")
    assert b"RIFF....fake wav payload" in seen["body"]
    assert b"meeting.16k.wav" in seen["body"]
    # and no trace of the old key-based contract
    assert b'"key"' not in seen["body"]


@pytest.mark.asyncio
async def test_language_is_optional_and_forwarded(tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x")
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(200, json={"language": "en", "duration_ms": 0,
                                         "segments": [], "text": ""})

    c = _client(handler)
    await c.transcribe(wav)
    await c.transcribe(wav, language="en")
    assert b"language" not in bodies[0]
    assert b'name="language"' in bodies[1] and b"en" in bodies[1]


@pytest.mark.asyncio
async def test_a_missing_file_fails_here_not_over_the_wire(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should never reach infer")

    with pytest.raises(FileNotFoundError):
        await _client(handler).transcribe(tmp_path / "gone.wav")
