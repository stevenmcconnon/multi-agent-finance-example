# harness/tools: the tools an agent may call (layer L5, harness)

Each file is one tool: a Pydantic input model, a `ToolSpec` (name, description, JSON schema, risk level,
required roles) and a `run()` that does exactly one thing. Nothing here talks to a model; the harness
runner (`../runner.py`) is the only caller and applies policy, approval, idempotency, timeouts and audit
before and after `run()`.

| File | Tool | Risk | Who may call it | What it does |
|---|---|---|---|---|
| `search_documents.py` | `search_documents` | read | every role | hybrid search over the caller's readable chunks (`retrieval/hybrid.py`); refuses a result whose `acl_filtered` is not true |
| `facts_sql.py` | `facts_sql` | read | analyst, admin | structured XBRL (eXtensible Business Reporting Language) facts by entity, concept and period, with the filing as provenance |
| `calculator.py` | `calculator` | read | analyst, admin | safe arithmetic over an abstract syntax tree (no names, no calls) |
| `create_ticket.py` | `create_ticket` | write | admin | the demo's one action: records a ticket in the audit log; needs a human's approval first |
| `__init__.py` | | | | `default_registry()` builds the registry the API and the MCP (Model Context Protocol) server share |

Rules: a write tool is never run without `approved=True`, which only the graph's human-approval path
passes (`tests/test_harness.py::test_write_tool_needs_explicit_approval`). Adding a tool means adding a
file here, a Cedar action in `db/policies/schema.cedarschema` and `tools.cedar`, and one test.

In production: tools call real systems (ticketing, email, payments) behind the same door, with the
`money` risk level requiring approval plus a second-model check. In this demo: the `money` level is reserved and no tool uses it; `create_ticket` writes an
audit row, because the point is the gate, not the ticket.
