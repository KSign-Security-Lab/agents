"""GPU sidecar: embeddings, reranking, Korean ASR.

Kept as one service rather than three because all three models share a single
GPU with vLLM, and one VRAM budget is easier to reason about than three.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException

from infer.app.config import settings
from infer.app.models import get_asr, get_embedder, get_reranker, loaded
from infer.app.schemas import (
    EmbedRequest,
    EmbedResponse,
    RerankRequest,
    RerankResponse,
    TranscribeRequest,
    TranscribeResponse,
    TranscriptSegment,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("infer")

app = FastAPI(title="agents-infer", version="0.1.0")

# The models are not asyncio-friendly, so every call is pushed to a worker
# thread. One semaphore per model serialises GPU access without blocking the
# event loop, which keeps /health responsive while a long batch is running.
_embed_sem = asyncio.Semaphore(1)
_rerank_sem = asyncio.Semaphore(1)
_asr_sem = asyncio.Semaphore(1)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "loaded": loaded(), "models": {
        "embed": settings.embed_model,
        "rerank": settings.rerank_model,
        "asr": settings.asr_model,
    }}


@app.get("/ready")
async def ready() -> dict:
    """Force-load everything. Used by `make pull-models` style warm-ups."""
    await asyncio.to_thread(get_embedder)
    await asyncio.to_thread(get_reranker)
    await asyncio.to_thread(get_asr)
    return {"status": "ready", "loaded": loaded()}


@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest) -> EmbedResponse:
    t0 = time.perf_counter()

    def _run() -> tuple[list[list[float]], list[dict[int, float]]]:
        model = get_embedder()
        out = model.encode(
            req.texts,
            batch_size=settings.embed_batch_size,
            max_length=settings.embed_max_length,
            return_dense=True,
            return_sparse=req.with_sparse,
            return_colbert_vecs=False,
        )
        dense = [v.tolist() if hasattr(v, "tolist") else list(v) for v in out["dense_vecs"]]
        sparse: list[dict[int, float]] = []
        if req.with_sparse:
            for lw in out.get("lexical_weights", []):
                # FlagEmbedding keys these by token id as a string; pgvector's
                # sparsevec wants int indices. Drop zero weights.
                sparse.append({int(k): float(v) for k, v in lw.items() if float(v) > 0})
        return dense, sparse

    async with _embed_sem:
        dense, sparse = await asyncio.to_thread(_run)

    log.info("embed n=%d sparse=%s %.0fms", len(req.texts), req.with_sparse,
             (time.perf_counter() - t0) * 1000)
    return EmbedResponse(dense=dense, sparse=sparse, dim=len(dense[0]) if dense else 0)


@app.post("/rerank", response_model=RerankResponse)
async def rerank(req: RerankRequest) -> RerankResponse:
    t0 = time.perf_counter()

    def _run() -> list[float]:
        model = get_reranker()
        pairs = [[req.query, p] for p in req.passages]
        scores = model.compute_score(pairs, batch_size=settings.rerank_batch_size,
                                     max_length=settings.rerank_max_length,
                                     normalize=True)
        # compute_score returns a bare float for a single pair.
        return [float(s) for s in (scores if isinstance(scores, list) else [scores])]

    async with _rerank_sem:
        scores = await asyncio.to_thread(_run)

    log.info("rerank n=%d %.0fms", len(req.passages), (time.perf_counter() - t0) * 1000)
    return RerankResponse(scores=scores)


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(req: TranscribeRequest) -> TranscribeResponse:
    src = (Path(settings.storage_root) / req.key).resolve()
    root = Path(settings.storage_root).resolve()
    if not src.is_relative_to(root):
        raise HTTPException(400, "key escapes storage root")
    if not src.exists():
        raise HTTPException(404, f"media not found: {req.key}")

    t0 = time.perf_counter()

    def _run() -> tuple[str, float, list[TranscriptSegment]]:
        model = get_asr()
        segments, info = model.transcribe(
            str(src),
            language=req.language or settings.asr_language,
            vad_filter=True,
            # Sentence-ish segments keep transcript citations tight enough that
            # seeking the player lands on the quoted moment.
            condition_on_previous_text=False,
            beam_size=5,
        )
        out: list[TranscriptSegment] = []
        for s in segments:
            text = (s.text or "").strip()
            if not text:
                continue
            out.append(TranscriptSegment(start_ms=int(s.start * 1000),
                                         end_ms=int(s.end * 1000), text=text))
        return info.language, info.duration, out

    async with _asr_sem:
        language, duration, segments = await asyncio.to_thread(_run)

    log.info("transcribe %s segs=%d %.0fms", req.key, len(segments),
             (time.perf_counter() - t0) * 1000)
    return TranscribeResponse(
        language=language,
        duration_ms=int(duration * 1000),
        segments=segments,
        text="\n".join(s.text for s in segments),
    )
