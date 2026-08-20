"""Client for the GPU sidecar (embeddings, reranking, ASR).

bge-m3 returns a dense vector and sparse lexical weights from a single forward
pass, which is why hybrid retrieval here needs neither a second model nor a
Korean full-text tokenizer in Postgres.
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from api.app.config import settings


class InferClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.infer_base_url).rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(600.0))

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        r = await self._client.post(path, json=payload)
        r.raise_for_status()
        return r.json()

    async def embed(self, texts: list[str], *, kind: str = "passage",
                    with_sparse: bool = True) -> dict[str, Any]:
        """``kind`` is "query" or "passage"; bge-m3 needs no instruction prefix,
        but the distinction is kept so a future model can use one."""
        if not texts:
            return {"dense": [], "sparse": []}
        return await self._post("/embed", {"texts": texts, "kind": kind,
                                           "with_sparse": with_sparse})

    async def embed_one(self, text: str, *, kind: str = "query",
                        with_sparse: bool = True) -> tuple[list[float], dict[str, float]]:
        out = await self.embed([text], kind=kind, with_sparse=with_sparse)
        sparse = out["sparse"][0] if out.get("sparse") else {}
        return out["dense"][0], sparse

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        out = await self._post("/rerank", {"query": query, "passages": passages})
        return out["scores"]

    async def transcribe(self, storage_key: str, *, language: str | None = None) -> dict[str, Any]:
        """The sidecar reads the media straight off the shared /storage mount, so
        a 2GB recording is never pushed through HTTP."""
        return await self._post("/transcribe", {"key": storage_key, "language": language})

    async def health(self) -> dict[str, Any]:
        r = await self._client.get("/health")
        r.raise_for_status()
        return r.json()

    async def close(self) -> None:
        await self._client.aclose()


infer_client = InferClient()
