"""Per-IP rate limiting with pluggable backends.

Default: in-process fixed 60-second windows (single worker only — each uvicorn
worker keeps its own counters). Set REDIS_URL to share windows across workers
and pods (fixed window via INCR + EXPIRE).

Both backends expose one coroutine:  allow(scope, ip, limit) -> bool.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

from config import settings

log = logging.getLogger(__name__)


class InProcessRateLimiter:
    """Fixed 60 s sliding-ish window kept in process memory.

    Keys are evicted when their window empties to prevent memory growth from
    unique IPs (important for internet-facing hospital/bank deployments).
    """

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def allow(self, scope: str, ip: str, limit: int) -> bool:
        key = f"{scope}:{ip}"
        now = time.monotonic()
        cutoff = now - 60
        window = self._hits[key]
        window[:] = [t for t in window if t > cutoff]
        if not window:
            self._hits.pop(key, None)
            window = self._hits[key]  # defaultdict re-creates on next access
        if len(window) >= limit:
            return False
        window.append(now)
        return True


class RedisRateLimiter:
    """Fixed 60 s window in Redis (INCR + EXPIRE) — shared across workers/pods.

    Fails open to an in-process limiter if Redis is unreachable, so a Redis
    outage degrades rate-limit accuracy rather than taking the API down.
    """

    def __init__(self, url: str) -> None:
        import redis.asyncio as redis  # optional dependency

        self._redis = redis.from_url(url, decode_responses=True)
        self._fallback = InProcessRateLimiter()
        self._warned = False

    async def allow(self, scope: str, ip: str, limit: int) -> bool:
        key = f"rl:{scope}:{ip}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, 60)
            return count <= limit
        except Exception as exc:
            if not self._warned:
                log.warning("Redis rate limiter unavailable (%s) — using in-process fallback", exc)
                self._warned = True
            return await self._fallback.allow(scope, ip, limit)


_limiter: InProcessRateLimiter | RedisRateLimiter | None = None


def get_limiter():
    """Process-lifetime limiter singleton, backend chosen by REDIS_URL."""
    global _limiter
    if _limiter is None:
        if settings.redis_url:
            try:
                _limiter = RedisRateLimiter(settings.redis_url)
                log.info("Rate limiting backed by Redis (%s)", settings.redis_url)
            except Exception as exc:
                log.warning("REDIS_URL set but Redis client failed (%s) — in-process limiter", exc)
                _limiter = InProcessRateLimiter()
        else:
            _limiter = InProcessRateLimiter()
    return _limiter
