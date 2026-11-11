# authz/

The authorization half of L5 Harness: Cedar decisions, the Principal-to-entity mapping, the group cache and the audit door.

## Files

| File | What it owns |
|---|---|
| `cedar_local.py` | `CedarLocalAuthorizer`: evaluates `db/policies/*.cedar` in process with `cedarpy`. |
| `verified_permissions.py` | `VerifiedPermissionsAuthorizer`: the same decision through Amazon Verified Permissions `is_authorized`. |
| `entities.py` | `Principal` to Cedar entities, shared by both backends. |
| `group_cache.py` | `GroupCache`: Redis time-to-live (TTL) cache of a user's groups and roles. |
| `audit.py` | Writes one `audit_events` row per decision, retrieval, tool call and answer. |
| `__init__.py` | `get_authorizer()`, chosen by `HR_AUTHZ_BACKEND`. |

## How it is used

`agent/team.py` (Guard) and `harness/runner.py` call `get_authorizer().decide(...)`. `harness/identity.py` resolves groups through `GroupCache`. Policies live in [`../../db/policies/`](../../../db/policies/README.md).

## Rules that hold here

- Any evaluation error denies: `PolicyDecision(allow=False, fail_closed=True)`. Tested by `test_authorizer_exception_fails_closed`, `test_malformed_resource_fails_closed`, `test_unknown_tool_fails_closed`, `test_avp_errors_fail_closed` in `tests/test_authz.py`.
- A cache miss or Redis failure reads the database; it is never "allow".
- A prompt cannot grant roles (`test_prompt_cannot_grant_roles`).
- Audit never raises into the caller; the decision was already taken fail-closed.
- Both backends see the same entities, so switching changes where Cedar runs, not what it decides.

## Production vs this demo

In production: Verified Permissions, so policy changes are audited and deployed as data.
In this demo: Cedar runs in process, because a local compose stack has no AWS.

## Read next

- [`../../db/policies/README.md`](../../../db/policies/README.md).
- [`../../docs/adr/0002-cedar-for-authorization.md`](../../../docs/adr/0002-cedar-for-authorization.md).
