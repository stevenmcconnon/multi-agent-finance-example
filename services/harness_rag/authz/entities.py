"""Principal -> Cedar entities. Shared by cedar_local and verified_permissions so both backends
see the same world; a difference between them would be a policy bug, not a mapping one."""

from __future__ import annotations

from typing import Any

from ..contracts import Principal

NAMESPACE = "HarnessRag"
USER = f"{NAMESPACE}::User"
GROUP = f"{NAMESPACE}::Group"
DOCUMENT = f"{NAMESPACE}::Document"
ORG = f"{NAMESPACE}::Org"
ACTION = f"{NAMESPACE}::Action"

KNOWN_ROLES = ("viewer", "analyst", "admin")


def acl_ids(principal: Principal) -> list[str]:
    """The principal's identities in ACL spelling, matching document_acl -> principals rows."""
    return [f"user:{principal.user_id}", *(f"group:{g}" for g in principal.groups)]


def principal_uid(principal: Principal) -> dict[str, str]:
    return {"type": USER, "id": principal.user_id}


def resource_uid(resource: str) -> dict[str, str]:
    """'Document::<uuid>' | 'Org::<uuid>' -> Cedar uid. Anything else raises (-> fail closed)."""
    kind, sep, ident = resource.partition("::")
    if not sep or not ident:
        raise ValueError(f"malformed resource {resource!r}")
    if kind in ("Document", DOCUMENT):
        return {"type": DOCUMENT, "id": ident}
    if kind in ("Org", ORG):
        return {"type": ORG, "id": ident}
    raise ValueError(f"unknown resource type {kind!r}")


def action_uid(action: str) -> dict[str, str]:
    if action not in ("retrieve", "answer") and not action.startswith("tool:"):
        raise ValueError(f"unknown action {action!r}")
    return {"type": ACTION, "id": action}


def build_entities(principal: Principal, resource: str, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Cedar entity list (JSON form) for one decision.

    The resource's ACL principals arrive in `context["readers"]` (a list of 'user:x' / 'group:y')
    and become the Document's `readers` attribute. A Document with no readers given denies.
    """
    ctx = context or {}
    org = str(principal.org_id)
    roles = sorted({r for r in principal.roles if r in KNOWN_ROLES})
    entities: list[dict[str, Any]] = [
        {
            "uid": principal_uid(principal),
            "attrs": {
                "org": org,
                "uid": principal.user_id,
                "groups": sorted(set(principal.groups)),
                "roles": roles,
                "acl_ids": acl_ids(principal),
            },
            "parents": [],
        }
    ]
    res = resource_uid(resource)
    if res["type"] == DOCUMENT:
        readers = ctx.get("readers") or []
        if not isinstance(readers, (list, tuple, set)) or not all(isinstance(r, str) for r in readers):
            raise ValueError("context.readers must be a list of 'user:<id>' / 'group:<name>' strings")
        resource_org = str(ctx.get("resource_org", org))
        entities.append({"uid": res, "attrs": {"org": resource_org, "readers": sorted(set(readers))}, "parents": []})
    else:
        entities.append({"uid": res, "attrs": {"org": res["id"]}, "parents": []})
    return entities


def build_request(principal: Principal, action: str, resource: str) -> dict[str, Any]:
    return {
        "principal": principal_uid(principal),
        "action": action_uid(action),
        "resource": resource_uid(resource),
        "context": {},
    }
