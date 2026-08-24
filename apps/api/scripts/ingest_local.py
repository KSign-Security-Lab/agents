"""Ingest a file from disk, bypassing the HTTP upload path.

Useful for testing the pipeline and for bulk-loading an existing folder of
documents without going through the browser.
"""
from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import sys
from pathlib import Path

from sqlalchemy import select

from api.app.db.models import Document, DocStatus
from api.app.db.session import SessionLocal
from api.app.ingest import convert
from api.app.ingest.pipeline import ingest_document
from api.app.services.storage import shard_key, storage


async def ingest_path(path: Path, *, force: bool = False) -> None:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    kind = convert.classify(path.name)

    async with SessionLocal() as db:
        existing = (await db.execute(
            select(Document).where(Document.sha256 == sha)
        )).scalar_one_or_none()

        if existing is None:
            key = shard_key("originals", sha, path.suffix.lower())
            storage.write_bytes(key, data)
            doc = Document(
                sha256=sha, filename=path.name,
                mime=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                size_bytes=len(data), source_kind=kind, status=DocStatus.pending,
                key_original=key,
            )
            db.add(doc)
            await db.commit()
            await db.refresh(doc)
        else:
            doc = existing
            print(f"  (already present as {doc.id}, re-running)")

        result = await ingest_document(db, doc.id, force=force or existing is not None)
        status = "OK " if result.ok else "FAIL"
        print(f"  {status} {path.name} -> {doc.status.value} ({result.detail})")


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = "--force" in sys.argv
    if not args:
        print("usage: python -m api.scripts.ingest_local <file> [file...] [--force]")
        raise SystemExit(2)
    for raw in args:
        p = Path(raw)
        if not p.exists():
            print(f"  SKIP {raw} (not found)")
            continue
        print(f"ingesting {p.name} ...")
        await ingest_path(p, force=force)


if __name__ == "__main__":
    asyncio.run(main())
