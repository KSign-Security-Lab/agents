"""Central settings. Every value is overridable by environment variable so the
same image runs in any topology (see docker/.env.example)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # ---- storage ---------------------------------------------------------
    storage_root: str = "/storage"
    max_upload_mb: int = 512

    # ---- database / cache ------------------------------------------------
    database_url: str = "postgresql+psycopg://agents:agents@postgres:5432/agents"
    redis_url: str = "redis://redis:6379/0"

    # ---- llm -------------------------------------------------------------
    llm_base_url: str = "http://llm-gateway/v1"
    llm_api_key: str = "dummy"
    llm_timeout_s: int = 300
    served_model_name: str = "main"
    max_model_len: int = 32768
    # Empty => the model has no vLLM tool-call parser, so tool calls are done
    # with guided JSON decoding instead. Set via docker/.env's TOOL_CALL_PARSER.
    tool_call_parser: str = ""

    # ---- inference sidecar ----------------------------------------------
    infer_base_url: str = "http://infer:8000"
    embed_dim: int = 1024
    sparse_dim: int = 250002

    # ---- ingest ----------------------------------------------------------
    ocr_engine: Literal["easyocr", "rapidocr", "tesseract"] = "easyocr"
    ocr_langs: str = "ko,en"
    # "direct" calls the OCR engine and keeps its line boxes; "docling" runs the
    # full Docling pipeline (recovers table cells on scans, but measured worse
    # text recall on Korean).
    ocr_layout_engine: Literal["direct", "docling"] = "direct"
    ocr_dpi: int = 200
    ocr_min_confidence: float = 0.25
    ocr_use_gpu: bool = False
    enable_keyframe_ocr: bool = False
    chunk_target_tokens: int = 512
    chunk_overlap_tokens: int = 64
    transcript_window_s: int = 45
    # Auto-merge only near-identical labels. Measured on bge-m3 with short
    # Korean topic labels: "계약" vs "계약/법무" scored 0.75 while every genuinely
    # distinct pair scored <= 0.57. A single global threshold in that gap would
    # be fragile as the taxonomy grows, so anything in the suggestion band is
    # offered to an admin instead of merged silently.
    topic_merge_threshold: float = 0.85
    topic_suggest_threshold: float = 0.70

    # ---- agent -----------------------------------------------------------
    max_subagents: int = 4
    max_retrieve_iterations: int = 3
    corpus_escalation: bool = True
    retrieve_top_k: int = 40
    rerank_top_k: int = 10
    sentence_align_threshold: float = 0.55
    web_search_enabled: bool = False
    web_search_provider: Literal["none", "tavily", "brave", "searxng"] = "none"
    web_search_api_key: str = ""

    # ---- auth ------------------------------------------------------------
    internal_jwt_secret: str = "dev-secret"
    internal_jwt_algorithm: str = "HS256"
    # The web tier keeps this token in an httpOnly cookie and refreshes it while
    # the user is active, so it doubles as the browser session lifetime.
    internal_jwt_ttl_minutes: int = 720

    @property
    def ocr_lang_list(self) -> list[str]:
        return [x.strip() for x in self.ocr_langs.split(",") if x.strip()]

    @property
    def sync_dsn(self) -> str:
        """Synchronous SQLAlchemy URL (psycopg3), used by Alembic.

        Note the driver must stay explicit: a bare ``postgresql://`` makes
        SQLAlchemy reach for psycopg2, which this image does not carry.
        """
        return self.database_url.replace("+psycopg", "").replace(
            "postgresql://", "postgresql+psycopg://"
        )

    @property
    def libpq_dsn(self) -> str:
        """Plain libpq DSN, for LangGraph's checkpointer and raw psycopg use."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
