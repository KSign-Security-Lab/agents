"""Redis pub/sub fan-out for the Slack-like shared sessions.

Every viewer of a session subscribes to one channel, so a message posted by one
person — and the assistant tokens streaming in reply — appear for everybody
watching, not just the author.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from api.app.config import settings

PRESENCE_TTL_S = 30


class Realtime:
    def __init__(self, url: str | None = None) -> None:
        self._redis = aioredis.from_url(url or settings.redis_url, decode_responses=True)

    # ------------------------------------------------------------- channels
    @staticmethod
    def session_channel(session_id: str) -> str:
        return f"session:{session_id}"

    @staticmethod
    def document_channel() -> str:
        """Ingest progress is workspace-wide: everyone sees uploads appear."""
        return "documents"

    # -------------------------------------------------------------- publish
    async def publish(self, channel: str, event: str, data: dict[str, Any]) -> None:
        await self._redis.publish(channel, json.dumps({"event": event, "data": data},
                                                      ensure_ascii=False, default=str))

    async def publish_session(self, session_id: str, event: str, data: dict[str, Any]) -> None:
        await self.publish(self.session_channel(session_id), event, data)

    async def publish_document(self, event: str, data: dict[str, Any]) -> None:
        await self.publish(self.document_channel(), event, data)

    # ------------------------------------------------------------ subscribe
    async def subscribe(self, *channels: str) -> AsyncIterator[dict[str, Any]]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(*channels)
        try:
            async for raw in pubsub.listen():
                if raw.get("type") != "message":
                    continue
                try:
                    yield json.loads(raw["data"])
                except json.JSONDecodeError:
                    continue
        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.aclose()

    # ------------------------------------------------------------- presence
    async def heartbeat(self, session_id: str, user_id: str, name: str) -> None:
        await self._redis.setex(f"presence:{session_id}:{user_id}", PRESENCE_TTL_S, name)

    async def viewers(self, session_id: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        async for key in self._redis.scan_iter(match=f"presence:{session_id}:*", count=100):
            name = await self._redis.get(key)
            if name:
                out.append({"user_id": key.rsplit(":", 1)[-1], "name": name})
        return out

    async def close(self) -> None:
        await self._redis.aclose()


realtime = Realtime()
