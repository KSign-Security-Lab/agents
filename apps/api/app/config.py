"""Central settings, and the single place URLs are assembled.

Defaults describe a developer's machine: api and web run on the host, Postgres
and Redis are the containers `docker compose up -d` starts, and the models are
served by GPU_HOST. A container needing different values (the ingest worker
reaching Postgres as a sibling) gets them from compose.worker.yaml, and an explicit
environment variable always beats anything derived here.

The composite URLs are built rather than configured: setting POSTGRES_PORT alone
used to leave DATABASE_URL on the old port, so the two could silently disagree.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/app/config.py -> apps/api -> apps -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    # One .env at the repo root, the same file docker compose reads. Absolute, so
    # it resolves whichever directory a command is run from.
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore",
                                      case_sensitive=False)

    # ---- where things live ----------------------------------------------
    # A node of the Kubernetes cluster serving the models. The ports are the
    # NodePorts from k8s/vllm.yaml and k8s/infer.yaml, so any node works — the
    # Service routes to whichever pod is ready.
    gpu_host: str = "localhost"
    llm_port: int = 30862
    infer_port: int = 30863
    postgres_user: str = "agents"
    postgres_password: str = "agents"
    postgres_db: str = "agents"
    postgres_port: int = 5433
    redis_port: int = 6380
    data_root: str = ""            # "" -> <repo>/data

    # ---- storage ---------------------------------------------------------
    storage_root: str = ""         # "" -> <data_root>/storage
    max_upload_mb: int = 512

    # ---- database / cache ------------------------------------------------
    # "" on any of these four means "assemble it from the parts above".
    database_url: str = ""
    redis_url: str = ""

    # ---- llm -------------------------------------------------------------
    llm_base_url: str = ""
    llm_api_key: str = "dummy"
    llm_timeout_s: int = 300
    served_model_name: str = "main"
    max_model_len: int = 32768
    # Empty => the model has no vLLM tool-call parser, so tool calls are done
    # with guided JSON decoding instead. Set via docker/.env's TOOL_CALL_PARSER.
    tool_call_parser: str = ""

    # ---- inference sidecar ----------------------------------------------
    infer_base_url: str = ""
    # No embed_dim: the vector column's width is fixed by the migration that
    # created it (Vector(1024)), so a setting here would be read by nothing and
    # would imply a model swap is a config change. It is a migration.
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

    # ---- auth ------------------------------------------------------------
    internal_jwt_secret: str = "dev-secret"
    internal_jwt_algorithm: str = "HS256"
    # The web tier keeps this token in an httpOnly cookie and refreshes it while
    # the user is active, so it doubles as the browser session lifetime.
    internal_jwt_ttl_minutes: int = 720

    # ---- first admin account (used by `pnpm seed`) ------------------------
    admin_email: str = "dev@agents.dev"
    admin_name: str = "관리자"
    admin_password: str = "devdev"

    @model_validator(mode="after")
    def _assemble(self) -> "Settings":
        if not self.data_root:
            self.data_root = str(REPO_ROOT / "data")
        if not self.storage_root:
            self.storage_root = str(Path(self.data_root) / "storage")
        if not self.database_url:
            self.database_url = (
                f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
                f"@localhost:{self.postgres_port}/{self.postgres_db}"
            )
        if not self.redis_url:
            self.redis_url = f"redis://localhost:{self.redis_port}/0"
        if not self.llm_base_url:
            self.llm_base_url = f"http://{self.gpu_host}:{self.llm_port}/v1"
        if not self.infer_base_url:
            self.infer_base_url = f"http://{self.gpu_host}:{self.infer_port}"
        return self

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
