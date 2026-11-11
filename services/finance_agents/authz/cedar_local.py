"""Local Cedar authorizer (cedarpy): policies from db/policies/*.cedar, schema from schema.cedarschema.

Decision contract (interfaces.Authorizer): any exception, malformed input or unknown action
yields PolicyDecision(allow=False, fail_closed=True, reasons=["authorizer_error"]).
Every decision is audited as kind="policy.decision".
"""

from __future__ import annotations

import logging
from pathlib import Path

import cedarpy

from ..contracts import PolicyDecision, Principal
from . import audit
from .entities import build_entities, build_request

log = logging.getLogger(__name__)

POLICIES_DIR = Path(__file__).resolve().parents[3] / "db" / "policies"


def load_policy_text(directory: Path = POLICIES_DIR) -> str:
    files = sorted(directory.glob("*.cedar"))
    if not files:
        raise FileNotFoundError(f"no .cedar policies under {directory}")
    return "\n\n".join(p.read_text() for p in files)


def load_schema_text(directory: Path = POLICIES_DIR) -> str:
    return (directory / "schema.cedarschema").read_text()


class CedarLocalAuthorizer:
    def __init__(self, policies_dir: Path = POLICIES_DIR, *, audit_decisions: bool = True) -> None:
        self._policy_text = load_policy_text(policies_dir)
        self._schema_text = load_schema_text(policies_dir)
        validation = cedarpy.validate_policies(self._policy_text, self._schema_text)
        if not validation.validation_passed:
            raise ValueError(f"cedar policies fail validation: {validation.errors}")
        # Pre-parsed handles: no re-parse per decision.
        self._policies = cedarpy.PolicySet.from_str(self._policy_text)
        self._schema = cedarpy.Schema.from_str(self._schema_text)
        self._audit = audit_decisions

    async def decide(
        self, principal: Principal, action: str, resource: str, context: dict | None = None
    ) -> PolicyDecision:
        try:
            request = build_request(principal, action, resource)
            entities = build_entities(principal, resource, context)
            result = cedarpy.is_authorized(request, self._policies, entities, self._schema)
            diag = result.diagnostics
            if diag.errors:
                # Evaluation errors (missing attribute etc.) mean a policy did not evaluate: treat as outage.
                raise RuntimeError(f"cedar evaluation errors: {diag.errors}")
            named = diag.id_annotations_by_reason
            policy_ids = [named.get(pid, pid) for pid in diag.reasons]
            decision = PolicyDecision(
                allow=bool(result.allowed),
                action=action,
                resource=resource,
                reasons=policy_ids if result.allowed else ["no_matching_permit"],
                policy_ids=policy_ids,
                fail_closed=False,
            )
        except Exception as exc:
            log.warning("authorizer error user=%s action=%s resource=%s: %s", principal.user_id, action, resource, exc)
            decision = PolicyDecision(
                allow=False, action=action, resource=resource, reasons=["authorizer_error"], fail_closed=True
            )
        if self._audit:
            await audit.record(
                "policy.decision",
                principal,
                {"backend": "cedar_local", **decision.model_dump()},
                thread_id=(context or {}).get("thread_id"),
            )
        return decision
