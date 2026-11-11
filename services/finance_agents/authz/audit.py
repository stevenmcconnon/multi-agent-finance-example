"""Audit door: every policy decision, retrieval and tool call writes one audit_events row.

Importers: authz (policy.decision), retrieval (retrieval), harness (tool.call, hitl, answer).
Auditing never raises into the caller: a failed audit write is logged, not fatal, because the
decision it records has already been taken fail-closed. Set `AUDIT_SINK` to a coroutine in tests.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from ..contracts import Principal
from ..guardrails.input import redact

log = logging.getLogger(__name__)

AuditSink = Callable[[str, Principal | None, dict[str, Any], str | None], Awaitable[None]]

AUDIT_KINDS = {"policy.decision", "retrieval", "tool.call", "model.call", "answer", "hitl", "ingest", "guardrail"}

# Test hook: when set, records go here instead of Postgres.
AUDIT_SINK: AuditSink | None = None


def _jsonable(value: Any) -> Any:
    """JSON-safe, and no PII: every string passes the guardrails redactor so no e-mail, phone, SSN or card
    lands in audit_events."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return redact(value)[0]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


async def record(kind: str, principal: Principal | None, payload: dict[str, Any], thread_id: str | None = None) -> None:
    """Write one audit_events row. `kind` is one of AUDIT_KINDS."""
    if kind not in AUDIT_KINDS:
        raise ValueError(f"unknown audit kind {kind!r}")
    body = _jsonable(payload)
    if AUDIT_SINK is not None:
        await AUDIT_SINK(kind, principal, body, thread_id)
        return
    try:
        from .. import db  # local import: tests of pure authz never touch Postgres

        await db.execute(
            "INSERT INTO audit_events (org_id, user_id, thread_id, kind, payload) VALUES (%s, %s, %s, %s, %s::jsonb)",
            (
                str(principal.org_id) if principal else None,
                principal.user_id if principal else None,
                thread_id,
                kind,
                json.dumps(body),
            ),
        )
    except Exception as exc:
        log.error("audit write failed kind=%s user=%s: %s", kind, principal.user_id if principal else None, exc)
