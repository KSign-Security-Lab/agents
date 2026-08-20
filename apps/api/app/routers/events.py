"""Workspace-wide event stream.

Uploads are visible to everyone, so ingest progress is broadcast rather than
returned only to the uploader — a teammate watching the document list sees new
files appear and move through their stages. Channel creation/archival is
broadcast the same way, so the channel-list sidebar updates live when someone
else creates one.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from api.app.deps import CurrentUser
from api.app.services.realtime import realtime

router = APIRouter(tags=["events"])


@router.get("/events")
async def workspace_events(request: Request, user: CurrentUser):
    async def gen():
        async for event in realtime.subscribe(realtime.document_channel(),
                                              realtime.channel_list_topic()):
            if await request.is_disconnected():
                break
            yield {"event": event["event"],
                   "data": json.dumps(event["data"], ensure_ascii=False, default=str)}

    return EventSourceResponse(gen(), ping=15000)
