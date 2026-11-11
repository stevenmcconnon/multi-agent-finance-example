"""Local dev tokens for the seeded identities. `uv run python -m harness_rag.harness.devtoken alice`.
The same `mint()` backs POST /auth/dev-token. Groups/roles in the token are a hint; the DB wins."""

from __future__ import annotations

import sys
import time

from jose import jwt

from ..config import settings
from .identity import LOCAL_ISSUER

# Mirrors db/migrations/0002_seed_dev.sql.
DEV_USERS = {
    "alice": {"groups": ["finance", "analysts"], "roles": ["analyst"]},
    "bob": {"groups": ["engineering"], "roles": ["viewer"]},
    "carol": {"groups": [], "roles": ["admin"]},
}


def mint(user: str, org: str = "acme", ttl_seconds: int = 8 * 3600) -> str:
    if user not in DEV_USERS:
        raise KeyError(f"unknown dev user {user!r}; one of {sorted(DEV_USERS)}")
    now = int(time.time())
    claims = {
        "sub": user,
        "org": org,
        **DEV_USERS[user],
        "iss": LOCAL_ISSUER,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(claims, settings.hr_jwt_secret, algorithm="HS256")


if __name__ == "__main__":
    print(mint(sys.argv[1] if len(sys.argv) > 1 else "alice"))
