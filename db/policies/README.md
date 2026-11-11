# db/policies/

The Cedar policies that decide who may do what. Cedar is Amazon's policy language: deny by default, a request is allowed only when a `permit` matches and no `forbid` does.

## Files

| File | What it owns |
|---|---|
| `schema.cedarschema` | Namespace `FinanceAgents`: entities `Org`, `User`, `Document`; the actions. |
| `documents.cedar` | Rules for reading documents and org-wide retrieval and answering. |
| `tools.cedar` | Rules for tool calls, and the cross-org `forbid`. |

## Entities and actions

- `User` carries `org`, `uid`, `groups`, `roles` (viewer, analyst, admin) and `acl_ids` (`user:<uid>` plus `group:<name>`), because Cedar has no `map()`.
- `Document` carries `org` and `readers`. `Org` is the resource for org-wide actions.
- Actions: `retrieve`, `answer`; `tool:search_documents`; `tool:facts_sql` and `tool:calculator` (in `ReadTools`); `tool:create_ticket` (in `WriteTools`).

## Which policy grants what

| Policy id | Grants |
|---|---|
| `document_acl_read` | Retrieve or answer on a document in your org when you or one of your groups is in its `readers`. |
| `org_read` | Retrieve or answer org-wide, any role. |
| `search_tool_any_role` | `tool:search_documents`, any role. |
| `read_tools_analyst_or_admin` | Read tools for analyst or admin. |
| `write_tools_admin` | Write tools for admin only. |
| `cross_org_forbid` | Always denies a resource from another org. |

## How it is used

`authz/cedar_local.py` loads these files and evaluates them in process; Terraform uploads the same text to Verified Permissions (`infra/terraform/modules/policy`; validated, not applied). `authz/entities.py` builds the entities.

## Rules that hold here

- A new tool needs an action in the schema and a place in `ReadTools` or `WriteTools`, or it fails closed. `test_unknown_tool_fails_closed` in `tests/test_authz.py`.
- Cedar permits are not the whole check: the SQL predicate and human approval apply on top.
- Both backends must load identical policy text.

## Read next

- [`../../services/finance_agents/authz/README.md`](../../services/finance_agents/authz/README.md).
- [`../../docs/adr/0002-cedar-for-authorization.md`](../../docs/adr/0002-cedar-for-authorization.md).
