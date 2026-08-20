"""One in-flight assistant run per channel.

Channels are shared, so two people can hit send at the same moment. Rather than
interleaving two answers into one thread, the second is queued and the UI shows
its position — which is also why ``MessageStatus.queued`` exists.
"""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import redis.asyncio as aioredis

from api.app.config import settings

RUN_LOCK_TTL_S = 900


class RunLock:
    def __init__(self, url: str | None = None) -> None:
        self._redis = aioredis.from_url(url or settings.redis_url, decode_responses=True)

    def _key(self, channel_id: str) -> str:
        return f"run:{channel_id}"

    async def acquire(self, channel_id: str, token: str) -> bool:
        return bool(await self._redis.set(self._key(channel_id), token,
                                          nx=True, ex=RUN_LOCK_TTL_S))

    async def release(self, channel_id: str, token: str) -> None:
        # Only the holder may release, so a timed-out run cannot free a newer one.
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        await self._redis.eval(script, 1, self._key(channel_id), token)

    async def holder(self, channel_id: str) -> str | None:
        return await self._redis.get(self._key(channel_id))

    @contextlib.asynccontextmanager
    async def hold(self, channel_id: str, token: str) -> AsyncIterator[bool]:
        got = await self.acquire(channel_id, token)
        try:
            yield got
        finally:
            if got:
                await self.release(channel_id, token)

    # ---- queue of pending turns, so the UI can show "2 waiting" -----------
    async def enqueue(self, channel_id: str, message_id: str) -> int:
        return int(await self._redis.rpush(f"queue:{channel_id}", message_id))

    async def dequeue(self, channel_id: str) -> str | None:
        return await self._redis.lpop(f"queue:{channel_id}")

    async def queue_depth(self, channel_id: str) -> int:
        return int(await self._redis.llen(f"queue:{channel_id}"))


run_lock = RunLock()
