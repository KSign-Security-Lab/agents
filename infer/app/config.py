from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    asr_model: str = "large-v3"
    asr_compute_type: str = "int8_float16"
    asr_language: str = "ko"

    storage_root: str = "/storage"
    device: str = "cuda"
    use_fp16: bool = True
    embed_batch_size: int = 8
    embed_max_length: int = 8192
    rerank_batch_size: int = 8
    rerank_max_length: int = 1024
    # Models load on first use, not at import, so the container reports healthy
    # quickly and a cold ASR model does not delay embedding traffic.
    eager_load: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
