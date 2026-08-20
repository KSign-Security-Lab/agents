"""FastAPI application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.app.config import settings
from api.app.routers import (
    admin,
    auth,
    documents,
    events,
    folders,
    search,
    sessions,
    topics,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("api starting: llm=%s infer=%s", settings.llm_base_url, settings.infer_base_url)
    yield
    from api.app.services.infer_client import infer_client
    from api.app.services.realtime import realtime

    await infer_client.close()
    await realtime.close()


app = FastAPI(
    title="문서 기반 에이전트 API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
)

# Only the Next.js tier calls this service, and it does so server-side; the
# permissive origin is for local development against the API directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, documents.router, folders.router, sessions.router,
          topics.router, search.router, events.router, admin.router):
    app.include_router(r)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict:
    """Report each dependency separately so a partial outage is diagnosable."""
    from sqlalchemy import text

    from api.app.db.session import engine
    from api.app.services.infer_client import infer_client
    from api.app.services.llm_client import client

    out: dict[str, object] = {"status": "ok", "checks": {}}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        out["checks"]["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        out["checks"]["postgres"] = f"error: {exc}"
        out["status"] = "degraded"

    try:
        out["checks"]["infer"] = (await infer_client.health()).get("status", "ok")
    except Exception as exc:  # noqa: BLE001
        out["checks"]["infer"] = f"error: {exc}"
        out["status"] = "degraded"

    try:
        models = await client().models.list()
        out["checks"]["llm"] = [m.id for m in models.data]
    except Exception as exc:  # noqa: BLE001
        out["checks"]["llm"] = f"error: {exc}"
        out["status"] = "degraded"

    return out


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})
