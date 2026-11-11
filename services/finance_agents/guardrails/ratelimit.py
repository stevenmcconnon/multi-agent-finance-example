"""Per-principal rate limit: Redis INCR + EXPIRE on `hr:rl:<org>:<user>:<minute>`, limit from
HR_RATE_LIMIT_PER_MINUTE. Shared by every API replica because the counter lives in Redis.

Availability over strictness: this is a read API behind authentication, so a Redis failure ALLOWS the
request and logs a warning rather than turning a cache outage into an API outage.
In production: the same counter, plus the load balancer's or API gateway's own request limits.
In this demo: a fixed one-minute window (one key per minute), because INCR+EXPIRE is two commands and
  the boundary burst it permits does not matter for a demo.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..config import settings
from ..contracts import Principal

log = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def _redis(self) -> Any:
        if self._client is None:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        return self._client

    async def retry_after(self, principal: Principal) -> int:
        """0 when the request may proceed, else the seconds until the window resets."""
        now = int(time.time())
        key = f"hr:rl:{principal.org_id}:{principal.user_id}:{now // 60}"
        try:
            count = await self._redis().incr(key)
            if count == 1:
                await self._redis().expire(key, 120)
        except Exception as exc:
            log.warning("rate limit unavailable, allowing: %s", exc)
            return 0
        return 60 - now % 60 if count > settings.hr_rate_limit_per_minute else 0


LIMITER = RateLimiter()
