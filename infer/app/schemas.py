from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1)
    kind: Literal["query", "passage"] = "passage"
    with_sparse: bool = True


class EmbedResponse(BaseModel):
    dense: list[list[float]]
    # Sparse lexical weights keyed by vocabulary id, ready to be written to a
    # pgvector `sparsevec` column.
    sparse: list[dict[int, float]] = []
    dim: int


class RerankRequest(BaseModel):
    query: str
    passages: list[str] = Field(min_length=1)


class RerankResponse(BaseModel):
    scores: list[float]


class TranscribeRequest(BaseModel):
    # Path relative to the shared /storage mount; the audio is never sent over HTTP.
    key: str
    language: str | None = None


class TranscriptSegment(BaseModel):
    start_ms: int
    end_ms: int
    text: str


class TranscribeResponse(BaseModel):
    language: str
    duration_ms: int
    segments: list[TranscriptSegment]
    text: str
