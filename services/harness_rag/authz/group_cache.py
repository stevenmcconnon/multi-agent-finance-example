"""Redis TTL cache for principal resolution (groups + roles), keyed by (org, user).

Semantics that matter for authorization:
  * a cache HIT is trusted for `ttl` seconds, including a cached "no groups" answer (a denial);
  * a cache MISS or any Redis failure resolves from the database; a failure is never "allow";
  * `invalidate(user_id)` drops the entry so a membership change takes effect at once.
Pass a `fakeredis.aioredis.FakeRedis` as `client` in tests.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from ..config import settings

log = logging.getLogger(__name__)

ROLE_PREFIX = "role:"  # roles are stored as groups named role:<role>; see 0002_seed_dev.sql

Resolver = Callable[[UUID, str], Awaitable[dict[str, list[str]]]]


def split_roles(group_names: list[str]) -> tuple[list[str], list[str]]:
    """['finance', 'role:analyst'] -> (['finance'], ['analyst'])."""
    groups = sorted({g for g in group_names if not g.startswith(ROLE_PREFIX)})
    roles = sorted({g[len(ROLE_PREFIX) :] for g in group_names if g.startswith(ROLE_PREFIX)})
    return groups, roles


async def resolve_from_db(org_id: UUID, user_id: str) -> dict[str, list[str]]:
    """The DB resolver: group_members -> principals (groups) for the user; roles are role:* groups."""
    from .. import db

    rows = await db.fetch(
        """
        SELECT g.external_id
        FROM principals u
        JOIN group_members gm ON gm.member_id = u.id
        JOIN principals g ON g.id = gm.group_id AND g.kind = 'group'
        WHERE u.org_id = %(org)s AND u.kind = 'user' AND u.external_id = %(user)s
        """,
        {"org": str(org_id), "user": user_id},
    )
    groups, roles = split_roles([r["external_id"] for r in rows])
    return {"groups": groups, "roles": roles}


class GroupCache:
    def __init__(self, client: Any | None = None, *, ttl_seconds: int = 300, resolver: Resolver = resolve_from_db):
        self._client = client
        self._ttl = ttl_seconds
        self._resolver = resolver

    def _redis(self) -> Any:
        if self._client is None:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(settings.redis_url, decode_responses=True)
        return self._client

    @staticmethod
    def key(org_id: UUID | str, user_id: str) -> str:
        return f"hr:principal:{org_id}:{user_id}"

    async def get(self, org_id: UUID, user_id: str) -> dict[str, list[str]]:
        """Groups and roles for the user; cached, else resolved and cached."""
        key = self.key(org_id, user_id)
        try:
            raw = await self._redis().get(key)
            if raw is not None:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                cached = json.loads(raw)
                return {"groups": list(cached.get("groups", [])), "roles": list(cached.get("roles", []))}
        except Exception as exc:
            log.warning("group cache read failed (resolving from db): %s", exc)
        resolved = await self._resolver(org_id, user_id)
        try:
            await self._redis().set(key, json.dumps(resolved), ex=self._ttl)
        except Exception as exc:
            log.warning("group cache write failed: %s", exc)
        return resolved

    async def invalidate(self, user_id: str, org_id: UUID | str | None = None) -> int:
        """Drop the cached resolution for a user (all orgs when org_id is None). Returns keys removed."""
        try:
            r = self._redis()
            if org_id is not None:
                return int(await r.delete(self.key(org_id, user_id)))
            removed = 0
            async for k in r.scan_iter(match=f"hr:principal:*:{user_id}"):
                removed += int(await r.delete(k))
            return removed
        except Exception as exc:
            log.warning("group cache invalidate failed for %s: %s", user_id, exc)
            return 0
