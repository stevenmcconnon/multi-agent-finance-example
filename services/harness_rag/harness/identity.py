"""JWT -> Principal. local: HS256 with HR_JWT_SECRET. cognito: JWKS (httpx, cached) + iss/aud/token_use check.

Who the caller IS comes from the token; what they MAY do comes from the database (authz.group_cache),
token groups are a hint only. `exp` is required in both modes. Any failure raises InvalidToken; callers
turn that into 401. Nothing here reads the prompt. Claim layout: docs/CONTRACTS.md "JWT".
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import httpx
from jose import jwt

from ..authz.group_cache import GroupCache
from ..config import Settings, settings
from ..contracts import Principal

LOCAL_ISSUER = "harness-rag-local"
ORG_GROUP_PREFIX = "org:"  # Cognito group `org:<slug>` names the user's org (Terraform modules/identity)


class InvalidToken(Exception):
    pass


def check_config() -> None:
    """Refuse to boot an AWS deployment on the local-dev identity defaults (audit finding 9)."""
    if settings.hr_mode == "aws":
        if settings.hr_jwt_mode == "local":
            raise RuntimeError("HR_MODE=aws needs HR_JWT_MODE=cognito; local mode would mint admin tokens to anyone")
        if settings.hr_jwt_secret == Settings.model_fields["hr_jwt_secret"].default:
            raise RuntimeError("HR_MODE=aws with the default HR_JWT_SECRET; set a real secret")


async def org_id_by_slug(slug: str) -> UUID:
    from .. import db

    rows = await db.fetch("SELECT id FROM orgs WHERE slug = %s", (slug,))
    if not rows:
        raise InvalidToken(f"unknown org {slug!r}")
    return rows[0]["id"]


OrgResolver = Callable[[str], Awaitable[UUID]]
_group_cache: GroupCache | None = None


def group_cache() -> GroupCache:
    global _group_cache
    if _group_cache is None:
        _group_cache = GroupCache()
    return _group_cache


@functools.lru_cache(maxsize=4)
def _jwks(pool_id: str, region: str) -> dict:
    url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json"
    return httpx.get(url, timeout=5).json()


def decode_claims(token: str) -> dict:
    """Signature + exp (required) + (cognito) iss/aud/token_use verified. Returns the raw claims."""
    try:
        if settings.hr_jwt_mode == "cognito":
            issuer = f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_user_pool_id}"
            claims = jwt.decode(
                token,
                _jwks(settings.cognito_user_pool_id, settings.cognito_region),
                algorithms=["RS256"],
                issuer=issuer,
                audience=settings.cognito_app_client_id,
                options={"require_exp": True},
            )
        else:
            claims = jwt.decode(
                token,
                settings.hr_jwt_secret,
                algorithms=["HS256"],
                issuer=LOCAL_ISSUER,
                options={"require_exp": True},
            )
    except Exception as exc:
        raise InvalidToken(str(exc)) from None
    if settings.hr_jwt_mode == "cognito" and claims.get("token_use") != "id":
        raise InvalidToken("not an ID token")
    return claims


def identity_from_claims(claims: dict) -> tuple[str, str]:
    """(user external_id, org slug). local: `sub`/`org`. cognito: `cognito:username` + the one `org:` group."""
    if settings.hr_jwt_mode == "cognito":
        user = claims.get("cognito:username")
        orgs = [g[len(ORG_GROUP_PREFIX) :] for g in claims.get("cognito:groups", []) if g.startswith(ORG_GROUP_PREFIX)]
        if not user or len(orgs) != 1:
            raise InvalidToken("token needs cognito:username and exactly one org:<slug> group")
        return user, orgs[0]
    user, org = claims.get("sub"), claims.get("org")
    if not user or not org:
        raise InvalidToken("token lacks sub/org")
    return user, org


async def verify_token(
    token: str, *, org_resolver: OrgResolver = org_id_by_slug, groups: GroupCache | None = None
) -> Principal:
    claims = decode_claims(token)
    user, org = identity_from_claims(claims)
    org_id = await org_resolver(org)
    try:
        resolved = await (groups or group_cache()).get(org_id, user)
    except Exception as exc:  # DB down -> no groups -> deny; never trust the token's hint instead
        raise InvalidToken(f"principal resolution failed: {exc}") from None
    iat = claims.get("iat")
    return Principal(
        user_id=user,
        org_id=org_id,
        org_slug=org,
        groups=resolved["groups"],
        roles=resolved["roles"],
        token_issued_at=datetime.fromtimestamp(iat, tz=UTC) if iat else None,
    )
