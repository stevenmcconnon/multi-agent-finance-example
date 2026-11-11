"""Amazon Verified Permissions authorizer: the same Principal -> entities mapping as cedar_local,
evaluated by AVP `is_authorized` against the policy store in settings.avp_policy_store_id.

The policies in db/policies/ are what gets uploaded to the store (same schema namespace), so
switching HR_AUTHZ_BACKEND changes WHERE Cedar runs, not WHAT it decides. Fail closed on any error.
Tests never call AWS: inject a `client` with the same `is_authorized(**kwargs)` shape.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import settings
from ..contracts import PolicyDecision, Principal
from . import audit
from .entities import build_entities, build_request

log = logging.getLogger(__name__)


def _to_avp_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolean": value}
    if isinstance(value, int):
        return {"long": value}
    if isinstance(value, str):
        return {"string": value}
    if isinstance(value, (list, tuple, set)):
        return {"set": [_to_avp_value(v) for v in value]}
    if isinstance(value, dict):
        return {"record": {k: _to_avp_value(v) for k, v in value.items()}}
    raise TypeError(f"unsupported attribute type {type(value).__name__}")


def _to_avp_entity(entity: dict[str, Any]) -> dict[str, Any]:
    uid = {"entityType": entity["uid"]["type"], "entityId": entity["uid"]["id"]}
    return {
        "identifier": uid,
        "attributes": {k: _to_avp_value(v) for k, v in entity["attrs"].items()},
        "parents": [{"entityType": p["type"], "entityId": p["id"]} for p in entity.get("parents", [])],
    }


class VerifiedPermissionsAuthorizer:
    def __init__(self, client: Any | None = None, policy_store_id: str | None = None, *, audit_decisions: bool = True):
        self._store = policy_store_id or settings.avp_policy_store_id
        if not self._store:
            raise ValueError("AVP_POLICY_STORE_ID is required for the verified_permissions backend")
        self._client = client
        self._audit = audit_decisions

    def _boto(self) -> Any:
        if self._client is None:
            import boto3  # lazy: tests never import a real client

            self._client = boto3.client("verifiedpermissions", region_name=settings.aws_region)
        return self._client

    async def decide(
        self, principal: Principal, action: str, resource: str, context: dict | None = None
    ) -> PolicyDecision:
        try:
            request = build_request(principal, action, resource)
            entities = build_entities(principal, resource, context)
            response = self._boto().is_authorized(
                policyStoreId=self._store,
                principal={
                    "entityType": request["principal"]["type"],
                    "entityId": request["principal"]["id"],
                },
                action={"actionType": request["action"]["type"], "actionId": request["action"]["id"]},
                resource={"entityType": request["resource"]["type"], "entityId": request["resource"]["id"]},
                entities={"entityList": [_to_avp_entity(e) for e in entities]},
            )
            if response.get("errors"):
                raise RuntimeError(f"avp evaluation errors: {response['errors']}")
            allowed = response.get("decision") == "ALLOW"
            policy_ids = [d.get("policyId", "") for d in response.get("determiningPolicies", [])]
            decision = PolicyDecision(
                allow=allowed,
                action=action,
                resource=resource,
                reasons=policy_ids if allowed else ["no_matching_permit"],
                policy_ids=policy_ids,
                fail_closed=False,
            )
        except Exception as exc:
            log.warning("avp authorizer error user=%s action=%s: %s", principal.user_id, action, exc)
            decision = PolicyDecision(
                allow=False, action=action, resource=resource, reasons=["authorizer_error"], fail_closed=True
            )
        if self._audit:
            await audit.record(
                "policy.decision",
                principal,
                {"backend": "verified_permissions", **decision.model_dump()},
                thread_id=(context or {}).get("thread_id"),
            )
        return decision
