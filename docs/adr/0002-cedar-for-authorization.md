# Architecture decision record (ADR) 0002: Cedar policies, evaluated in process locally and by Verified Permissions on Amazon Web Services (AWS)

Status: accepted · 2026-11

## Context

Three kinds of authorization question: may this principal be answered at all, may this principal read this
document, may this principal call this tool. Roles (viewer, analyst, admin) and group membership both
matter. The rules must be reviewable by someone who is not reading Python, and the same rules must hold in
the local $0 stack and on AWS.

## Decision

Policies are Cedar (`db/policies/*.cedar`, schema in `schema.cedarschema`). The `Authorizer` port has two
adapters over one entity mapping (`authz/entities.py`): `CedarLocalAuthorizer` (cedarpy, in process) and
`VerifiedPermissionsAuthorizer` (Amazon Verified Permissions `is_authorized`). Terraform uploads the same
policy files to the store. Both fail closed on any error and audit every decision. Row-level document
filtering stays in Structured Query Language (SQL) (`ADR 0001`); Cedar decides the org-wide and tool questions, and the per-document
policy exists for single-document checks.

    In production: Verified Permissions, so policy changes are audited and deployed as data.
    In this demo: cedarpy in process by default, because a $0 stack cannot call AWS per decision.

## Alternatives

- **Python `if` statements per route.** Fastest to write; the rules scatter, a reviewer must read code, and
  the local and AWS behaviour would be two implementations.
- **Open Policy Agent (OPA) / Rego.** A capable engine with its own sidecar; no managed AWS service to switch to, and Rego is
  further from the shape of "principal, action, resource" these rules take.
- **Postgres row-level security (RLS) only.** Covers documents well, tools and org-wide questions poorly, and ties identity to a
  database role per request.

## Consequences

- `HR_AUTHZ_BACKEND` changes where Cedar runs, not what it decides; `tests/test_authz.py` runs the real
  local engine and a fake Amazon Verified Permissions (AVP) client against the same cases.
- Cedar has no map(), so the authorizer precomputes `acl_ids` (`user:<id>`, `group:<name>`) for
  `containsAny`. Unknown roles are dropped before evaluation.
- Every decision costs an audit row; at high queries per second (QPS) the audit writer becomes a batch.
