"""Pure-python tests of the Cedar authorizer, the entity mapping and the group cache. No Postgres."""

from __future__ import annotations

from uuid import UUID

import pytest
from finance_agents.authz import audit
from finance_agents.authz.cedar_local import CedarLocalAuthorizer
from finance_agents.authz.group_cache import GroupCache, split_roles
from finance_agents.authz.verified_permissions import VerifiedPermissionsAuthorizer
from finance_agents.contracts import Principal

ORG = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORG = UUID("00000000-0000-0000-0000-000000000002")
DOC = "Document::7b6f1c3e-0000-4000-8000-000000000001"


def principal(user: str, groups: list[str], roles: list[str], org: UUID = ORG) -> Principal:
    return Principal(user_id=user, org_id=org, org_slug="acme", groups=groups, roles=roles)


ALICE = principal("alice", ["finance", "analysts"], ["analyst"])
BOB = principal("bob", ["engineering"], ["viewer"])
CAROL = principal("carol", [], ["admin"])


@pytest.fixture(autouse=True)
def capture_audit(monkeypatch):
    events: list[tuple] = []

    async def sink(kind, principal, payload, thread_id):
        events.append((kind, principal, payload, thread_id))

    monkeypatch.setattr(audit, "AUDIT_SINK", sink)
    return events


@pytest.fixture(scope="module")
def authz() -> CedarLocalAuthorizer:
    return CedarLocalAuthorizer()


async def test_alice_allowed_via_group_acl(authz, capture_audit):
    d = await authz.decide(ALICE, "retrieve", DOC, {"readers": ["group:finance"]})
    assert d.allow is True and d.fail_closed is False
    assert "document_acl_read" in d.policy_ids
    assert capture_audit[-1][0] == "policy.decision" and capture_audit[-1][2]["allow"] is True


async def test_alice_allowed_via_user_acl(authz):
    d = await authz.decide(ALICE, "answer", DOC, {"readers": ["user:alice"]})
    assert d.allow is True


async def test_bob_denied_not_in_acl(authz):
    d = await authz.decide(BOB, "retrieve", DOC, {"readers": ["group:finance", "user:alice"]})
    assert d.allow is False and d.fail_closed is False
    assert d.reasons == ["no_matching_permit"]


async def test_no_readers_denies(authz):
    d = await authz.decide(ALICE, "retrieve", DOC, {})
    assert d.allow is False


async def test_cross_org_document_denied_even_when_in_acl(authz):
    d = await authz.decide(ALICE, "retrieve", DOC, {"readers": ["group:finance"], "resource_org": str(OTHER_ORG)})
    assert d.allow is False


async def test_org_wide_retrieve(authz):
    assert (await authz.decide(BOB, "retrieve", f"Org::{ORG}")).allow is True
    assert (await authz.decide(BOB, "retrieve", f"Org::{OTHER_ORG}")).allow is False


async def test_facts_sql_tool_requires_analyst_or_admin(authz):
    org = f"Org::{ORG}"
    assert (await authz.decide(BOB, "tool:facts_sql", org)).allow is False
    assert (await authz.decide(ALICE, "tool:facts_sql", org)).allow is True
    assert (await authz.decide(CAROL, "tool:facts_sql", org)).allow is True


async def test_write_tools_require_admin(authz):
    org = f"Org::{ORG}"
    assert (await authz.decide(ALICE, "tool:create_ticket", org)).allow is False
    assert (await authz.decide(CAROL, "tool:create_ticket", org)).allow is True
    assert (await authz.decide(CAROL, "tool:create_ticket", org)).allow is True


async def test_unknown_tool_fails_closed(authz):
    d = await authz.decide(CAROL, "tool:drop_database", f"Org::{ORG}")
    assert d.allow is False and d.fail_closed is True and d.reasons == ["authorizer_error"]


async def test_malformed_resource_fails_closed(authz):
    d = await authz.decide(ALICE, "retrieve", "Bucket::whatever")
    assert d.allow is False and d.fail_closed is True and d.reasons == ["authorizer_error"]


async def test_authorizer_exception_fails_closed(authz, monkeypatch, capture_audit):
    import finance_agents.authz.cedar_local as mod

    def boom(*a, **kw):
        raise RuntimeError("cedar engine down")

    monkeypatch.setattr(mod.cedarpy, "is_authorized", boom)
    d = await authz.decide(ALICE, "retrieve", DOC, {"readers": ["group:finance"]})
    assert d.allow is False and d.fail_closed is True and d.reasons == ["authorizer_error"]
    assert capture_audit[-1][2]["fail_closed"] is True


async def test_prompt_cannot_grant_roles(authz):
    """Roles outside viewer|analyst|admin are dropped by the mapping, not evaluated."""
    p = principal("mallory", [], ["superuser", "root"])
    assert (await authz.decide(p, "retrieve", f"Org::{ORG}")).allow is False


# ---- Verified Permissions adapter, with a fake boto client (no AWS) ----
class _FakeAvp:
    def __init__(self, decision="ALLOW", errors=None, raise_exc=False):
        self.decision, self.errors, self.raise_exc, self.calls = decision, errors or [], raise_exc, []

    def is_authorized(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc:
            raise RuntimeError("AccessDeniedException")
        return {"decision": self.decision, "determiningPolicies": [{"policyId": "p1"}], "errors": self.errors}


async def test_avp_allow_and_entity_shape():
    fake = _FakeAvp()
    a = VerifiedPermissionsAuthorizer(client=fake, policy_store_id="store-1")
    d = await a.decide(ALICE, "retrieve", DOC, {"readers": ["group:finance"]})
    assert d.allow is True and d.policy_ids == ["p1"]
    call = fake.calls[0]
    assert call["policyStoreId"] == "store-1"
    assert call["principal"] == {"entityType": "HarnessRag::User", "entityId": "alice"}
    user_entity = call["entities"]["entityList"][0]
    assert {"string": "group:finance"} in user_entity["attributes"]["acl_ids"]["set"]


async def test_avp_errors_fail_closed():
    for fake in (_FakeAvp(raise_exc=True), _FakeAvp(errors=[{"errorDescription": "x"}])):
        a = VerifiedPermissionsAuthorizer(client=fake, policy_store_id="store-1")
        d = await a.decide(ALICE, "retrieve", DOC, {"readers": ["group:finance"]})
        assert d.allow is False and d.fail_closed is True


# ---- group cache ----
def test_split_roles():
    assert split_roles(["finance", "role:analyst", "analysts"]) == (["analysts", "finance"], ["analyst"])


async def test_group_cache_hit_miss_and_invalidate():
    fakeredis = pytest.importorskip("fakeredis")
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    calls = []

    async def resolver(org_id, user_id):
        calls.append(user_id)
        return {"groups": ["finance"], "roles": ["analyst"]}

    cache = GroupCache(r, ttl_seconds=300, resolver=resolver)
    assert await cache.get(ORG, "alice") == {"groups": ["finance"], "roles": ["analyst"]}
    assert await cache.get(ORG, "alice") == {"groups": ["finance"], "roles": ["analyst"]}
    assert calls == ["alice"]  # second call served from cache
    assert await r.ttl(GroupCache.key(ORG, "alice")) > 0
    assert await cache.invalidate("alice") == 1
    await cache.get(ORG, "alice")
    assert calls == ["alice", "alice"]


async def test_group_cache_caches_denial_and_survives_redis_failure():
    class Broken:
        async def get(self, k):
            raise ConnectionError("redis down")

        async def set(self, *a, **kw):
            raise ConnectionError("redis down")

    async def resolver(org_id, user_id):
        return {"groups": [], "roles": []}

    cache = GroupCache(Broken(), resolver=resolver)
    assert await cache.get(ORG, "nobody") == {"groups": [], "roles": []}  # from DB resolver, never "allow"
