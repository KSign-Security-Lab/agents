"""File storage behind a narrow protocol.

A local filesystem implementation is enough for a single-node deployment and
avoids running an object store on a host whose root disk is nearly full. Files
are always served *through* the API so authentication applies; nothing is
exposed by a public URL. ``S3Storage`` is the seam for a later move.
"""
from __future__ import annotations

import hashlib
import shutil
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import BinaryIO

from api.app.config import settings

CHUNK = 1024 * 1024


# One implementation, and nothing annotates against an interface, so there is no
# Storage Protocol here. If a second backend ever arrives — S3, say — extract one
# then, from two real shapes rather than a guess at what they will share.
class LocalStorage:
    """Keys are relative POSIX paths under ``root``, e.g.
    ``originals/ab/cd/<sha>.pdf``."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.storage_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        # Refuse anything that escapes the storage root.
        if not p.is_relative_to(self.root):
            raise ValueError(f"key escapes storage root: {key!r}")
        return p

    def write(self, key: str, src: BinaryIO) -> int:
        dst = self.path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with dst.open("wb") as fh:
            while True:
                buf = src.read(CHUNK)
                if not buf:
                    break
                fh.write(buf)
                written += len(buf)
        return written

    def write_bytes(self, key: str, data: bytes) -> int:
        dst = self.path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return len(data)

    def read_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def copy_in(self, key: str, src_path: str | Path) -> int:
        dst = self.path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_path, dst)
        return dst.stat().st_size

    def stream(self, key: str, chunk: int = CHUNK) -> Iterable[bytes]:
        with self.path(key).open("rb") as fh:
            while True:
                buf = fh.read(chunk)
                if not buf:
                    return
                yield buf


def shard_key(prefix: str, sha256: str, suffix: str) -> str:
    """Two-level fan-out keeps directory listings small at 3k+ documents."""
    return f"{prefix}/{sha256[:2]}/{sha256[2:4]}/{sha256}{suffix}"


async def hash_and_store(storage: LocalStorage, prefix: str, suffix: str,
                         stream: AsyncIterator[bytes]) -> tuple[str, str, int]:
    """Stream an upload to a temp file while hashing, then move it into its
    content-addressed key. Returns ``(sha256, key, size)``.

    Hashing during the write means we never hold a 500MB upload in memory and
    never need a second pass over the file to deduplicate it.
    """
    digest = hashlib.sha256()
    tmp_key = f"tmp/{next_token()}{suffix}"
    tmp_path = storage.path(tmp_key)
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with tmp_path.open("wb") as fh:
            async for buf in stream:
                fh.write(buf)
                digest.update(buf)
                size += len(buf)
        sha = digest.hexdigest()
        key = shard_key(prefix, sha, suffix)
        final = storage.path(key)
        final.parent.mkdir(parents=True, exist_ok=True)
        # A duplicate upload of identical bytes is a no-op.
        if final.exists():
            tmp_path.unlink(missing_ok=True)
        else:
            tmp_path.replace(final)
        return sha, key, size
    finally:
        tmp_path.unlink(missing_ok=True)


def next_token() -> str:
    import secrets

    return secrets.token_hex(16)


storage = LocalStorage()
