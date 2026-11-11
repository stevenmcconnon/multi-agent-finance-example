"""Offline: JWT -> Principal in local and Cognito mode, and the AWS boot guard (audit findings 1 and 9).
No DB: the org resolver and the group resolver are stubbed; the JWKS fetch is monkeypatched."""

from __future__ import annotations

import time
import uuid
from uuid import UUID

import fakeredis.aioredis
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from harness_rag.authz.group_cache import GroupCache
from harness_rag.harness import devtoken, identity
from jose import jwk, jwt

ORG = UUID("00000000-0000-0000-0000-000000000001")
POOL, REGION, CLIENT = "us-east-1_TEST", "us-east-1", "test-client-id"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
PUBLIC_PEM = (
    _key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
)
JWKS = {"keys": [{**jwk.construct(PUBLIC_PEM, algorithm="RS256").to_dict(), "kid": "test-kid"}]}


async def org_resolver(slug: str) -> UUID:
    if slug != "acme":
        raise identity.InvalidToken(f"unknown org {slug!r}")
    return ORG


async def resolve_stub(org_id: UUID, user_id: str) -> dict[str, list[str]]:
    return (
        {"groups": ["finance", "analysts"], "roles": ["analyst"]} if user_id == "alice" else {"groups": [], "roles": []}
    )


def groups() -> GroupCache:
    return GroupCache(fakeredis.aioredis.FakeRedis(), resolver=resolve_stub)


async def verify(token: str):
    return await identity.verify_token(token, org_resolver=org_resolver, groups=groups())


def local_token(**claims) -> str:
    base = {"sub": "alice", "org": "acme", "iss": identity.LOCAL_ISSUER, "iat": int(time.time())}
    return jwt.encode({**base, **claims}, identity.settings.hr_jwt_secret, algorithm="HS256")


def cognito_token(**overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": str(uuid.uuid4()),
        "cognito:username": "alice",
        "cognito:groups": ["org:acme", "finance", "analysts", "role:analyst"],
        "iss": ISSUER,
        "aud": CLIENT,
        "token_use": "id",
        "iat": now,
        "exp": now + 600,
        **overrides,
    }
    return jwt.encode(claims, PRIVATE_PEM, algorithm="RS256", headers={"kid": "test-kid"})


@pytest.fixture
def local_mode(monkeypatch):
    monkeypatch.setattr(identity.settings, "hr_jwt_mode", "local")


@pytest.fixture
def cognito_mode(monkeypatch):
    monkeypatch.setattr(identity.settings, "hr_jwt_mode", "cognito")
    monkeypatch.setattr(identity.settings, "cognito_user_pool_id", POOL)
    monkeypatch.setattr(identity.settings, "cognito_region", REGION)
    monkeypatch.setattr(identity.settings, "cognito_app_client_id", CLIENT)
    monkeypatch.setattr(identity, "_jwks", lambda pool_id, region: JWKS)


async def test_local_dev_token_verifies(local_mode):
    p = await verify(devtoken.mint("alice"))
    assert (p.user_id, p.org_id, p.org_slug) == ("alice", ORG, "acme")
    assert p.groups == ["finance", "analysts"] and p.roles == ["analyst"]
    assert p.token_issued_at is not None


async def test_local_missing_exp_rejected(local_mode):
    with pytest.raises(identity.InvalidToken):
        await verify(local_token())


async def test_local_expired_rejected(local_mode):
    with pytest.raises(identity.InvalidToken):
        await verify(local_token(exp=int(time.time()) - 5))


async def test_cognito_token_maps_username_and_org_group(cognito_mode):
    p = await verify(cognito_token())
    assert (p.user_id, p.org_id, p.org_slug) == ("alice", ORG, "acme")
    assert p.groups == ["finance", "analysts"] and p.roles == ["analyst"]  # from the resolver, not the token


async def test_cognito_token_groups_are_only_a_hint(cognito_mode):
    p = await verify(cognito_token(**{"cognito:username": "bob", "cognito:groups": ["org:acme", "role:admin"]}))
    assert p.roles == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"cognito:groups": ["finance"]},  # no org: group
        {"cognito:groups": ["org:acme", "org:other"]},  # two org: groups
        {"aud": "someone-else"},
        {"iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OTHER"},
        {"token_use": "access"},
        {"exp": int(time.time()) - 5},
        {"cognito:username": ""},
    ],
)
async def test_cognito_token_rejected(cognito_mode, overrides):
    with pytest.raises(identity.InvalidToken):
        await verify(cognito_token(**overrides))


async def test_cognito_missing_exp_rejected(cognito_mode):
    now = int(time.time())
    claims = {"sub": "x", "cognito:username": "alice", "cognito:groups": ["org:acme"], "iss": ISSUER, "aud": CLIENT}
    claims |= {"token_use": "id", "iat": now}
    with pytest.raises(identity.InvalidToken):
        await verify(jwt.encode(claims, PRIVATE_PEM, algorithm="RS256", headers={"kid": "test-kid"}))


async def test_cognito_wrong_key_rejected(cognito_mode):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = other.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    now = int(time.time())
    claims = {"cognito:username": "alice", "cognito:groups": ["org:acme"], "iss": ISSUER, "aud": CLIENT}
    claims |= {"token_use": "id", "exp": now + 600}
    with pytest.raises(identity.InvalidToken):
        await verify(jwt.encode(claims, pem, algorithm="RS256", headers={"kid": "test-kid"}))


def test_check_config_refuses_aws_with_local_jwt(monkeypatch):
    monkeypatch.setattr(identity.settings, "hr_mode", "aws")
    monkeypatch.setattr(identity.settings, "hr_jwt_mode", "local")
    monkeypatch.setattr(identity.settings, "hr_jwt_secret", "a-real-secret")
    with pytest.raises(RuntimeError):
        identity.check_config()


def test_check_config_refuses_aws_with_default_secret(monkeypatch):
    monkeypatch.setattr(identity.settings, "hr_mode", "aws")
    monkeypatch.setattr(identity.settings, "hr_jwt_mode", "cognito")
    monkeypatch.setattr(identity.settings, "hr_jwt_secret", "dev-only-change-me")
    with pytest.raises(RuntimeError):
        identity.check_config()


def test_check_config_allows_local_defaults_and_proper_aws(monkeypatch):
    monkeypatch.setattr(identity.settings, "hr_mode", "local")
    identity.check_config()
    monkeypatch.setattr(identity.settings, "hr_mode", "aws")
    monkeypatch.setattr(identity.settings, "hr_jwt_mode", "cognito")
    monkeypatch.setattr(identity.settings, "hr_jwt_secret", "a-real-secret")
    identity.check_config()
