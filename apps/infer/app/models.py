"""Lazy, thread-safe holders for the three GPU models.

All three share one GPU with vLLM, so they are loaded on first use and each is
guarded by its own lock: a cold Whisper load must not block embedding requests
that the ingest pipeline is waiting on.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from infer.app.config import settings

log = logging.getLogger("infer.models")

_embed: Any = None
_rerank: Any = None
_asr: Any = None
_embed_lock = threading.Lock()
_rerank_lock = threading.Lock()
_asr_lock = threading.Lock()


def get_embedder() -> Any:
    global _embed
    if _embed is None:
        with _embed_lock:
            if _embed is None:
                from FlagEmbedding import BGEM3FlagModel

                log.info("loading embedder %s", settings.embed_model)
                _embed = BGEM3FlagModel(settings.embed_model, use_fp16=settings.use_fp16,
                                        devices=settings.device)
                log.info("embedder ready")
    return _embed


def get_reranker() -> Any:
    global _rerank
    if _rerank is None:
        with _rerank_lock:
            if _rerank is None:
                from FlagEmbedding import FlagReranker

                log.info("loading reranker %s", settings.rerank_model)
                _rerank = FlagReranker(settings.rerank_model, use_fp16=settings.use_fp16,
                                       devices=settings.device)
                log.info("reranker ready")
    return _rerank


def get_asr() -> Any:
    global _asr
    if _asr is None:
        with _asr_lock:
            if _asr is None:
                from faster_whisper import WhisperModel

                log.info("loading ASR %s (%s)", settings.asr_model, settings.asr_compute_type)
                _asr = WhisperModel(settings.asr_model, device=settings.device,
                                    compute_type=settings.asr_compute_type)
                log.info("ASR ready")
    return _asr


def loaded() -> dict[str, bool]:
    return {"embed": _embed is not None, "rerank": _rerank is not None, "asr": _asr is not None}
