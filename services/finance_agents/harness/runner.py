"""run_tool: the ONE door every tool call goes through (agent, MCP, API alike).

Order: Cedar `tool:<name>` on the caller's Org (deny -> forbidden, audited) -> approval (a write/money-risk
tool runs only with `approved=True`, which only the graph's hitl path passes after a human said yes; MCP and
other callers get `approval_required`) -> idempotency reservation -> timeout + tenacity retry (transient errors
only) inside a per-tool pybreaker -> audit `tool.call`.

Idempotency: a keyed call RESERVES its key before running (`INSERT ... ON CONFLICT DO NOTHING RETURNING`), so
two concurrent calls with one key run the tool once; the loser reads the winner's stored response, waiting up
to `IN_PROGRESS_WAIT_S` for it, else error="in_progress". A failed run of an idempotent tool releases the key
(safe to retry); a failed non-idempotent run keeps its result on the key, so it is never re-run by accident.
`IDEMPOTENCY` is a dict in tests (None value = reserved, running); None means the idempotency_keys table.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from typing import Any

import pybreaker
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..authz import audit, get_authorizer
from ..contracts import Principal, RiskLevel, ToolCall, ToolResult
from ..interfaces import Authorizer
from .registry import ToolRegistry

log = logging.getLogger(__name__)


class TransientError(Exception):
    """Raise from a tool for a retryable failure (vendor 5xx, connection reset)."""


RETRY_ON = (TransientError, TimeoutError, ConnectionError)
IDEMPOTENCY: dict[str, dict[str, Any] | None] | None = None
IN_PROGRESS_WAIT_S = 2.0
_breakers: dict[str, pybreaker.CircuitBreaker] = {}


@functools.lru_cache(maxsize=1)
def authorizer() -> Authorizer:
    """One authorizer per process: cedar_local parses the policy files in its constructor."""
    return get_authorizer()


def breaker_for(tool: str) -> pybreaker.CircuitBreaker:
    if tool not in _breakers:
        _breakers[tool] = pybreaker.CircuitBreaker(
            fail_max=5, reset_timeout=30, name=tool, throw_new_error_on_trip=False
        )
    return _breakers[tool]


async def _idem_reserve(key: str, tool: str) -> bool:
    """True when this call owns the key (nobody ran or is running it)."""
    if IDEMPOTENCY is not None:
        if key in IDEMPOTENCY:
            return False
        IDEMPOTENCY[key] = None
        return True
    from .. import db

    rows = await db.fetch(
        "INSERT INTO idempotency_keys (key, tool) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING RETURNING key",
        (key, tool),
    )
    return bool(rows)


async def _idem_get(key: str) -> dict[str, Any] | None:
    if IDEMPOTENCY is not None:
        return IDEMPOTENCY.get(key)
    from .. import db

    rows = await db.fetch("SELECT response FROM idempotency_keys WHERE key = %s", (key,))
    return rows[0]["response"] if rows else None


async def _idem_wait(key: str) -> dict[str, Any] | None:
    """The stored response of a key someone else reserved; None if it is still running after the wait."""
    deadline = time.monotonic() + IN_PROGRESS_WAIT_S
    while (cached := await _idem_get(key)) is None and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    return cached


async def _idem_set(key: str, response: dict[str, Any] | None) -> None:
    """Store the outcome on a reserved key; None releases the reservation."""
    if IDEMPOTENCY is not None:
        if response is None:
            IDEMPOTENCY.pop(key, None)
        else:
            IDEMPOTENCY[key] = response
        return
    from .. import db

    if response is None:
        await db.execute("DELETE FROM idempotency_keys WHERE key = %s", (key,))
    else:
        await db.execute("UPDATE idempotency_keys SET response = %s::jsonb WHERE key = %s", (json.dumps(response), key))


async def run_tool(
    principal: Principal, call: ToolCall, thread_id: str | None, registry: ToolRegistry, *, approved: bool = False
) -> ToolResult:
    started = time.perf_counter()
    org = f"Org::{principal.org_id}"
    decision = await authorizer().decide(principal, f"tool:{call.tool}", org, {"thread_id": thread_id})
    if not decision.allow:
        result = ToolResult(tool=call.tool, ok=False, error="forbidden")
        await _audit(principal, call, result, thread_id, started)
        return result

    tool = registry.get(call.tool)
    if tool.spec.risk != RiskLevel.read and not approved:
        result = ToolResult(tool=call.tool, ok=False, error="approval_required")
        await _audit(principal, call, result, thread_id, started)
        return result

    key = f"{principal.org_id}:{call.tool}:{call.idempotency_key}" if call.idempotency_key else None
    if key and not await _idem_reserve(key, call.tool):
        if (cached := await _idem_wait(key)) is not None:
            return ToolResult(**cached).model_copy(update={"from_cache": True})
        return ToolResult(tool=call.tool, ok=False, error="in_progress")

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            reraise=True,
            wait=wait_exponential(multiplier=0.05, max=1),
            retry=retry_if_exception_type(RETRY_ON),
        ):
            with attempt:
                result = await _guarded(breaker_for(call.tool), tool, principal, call)
    except pybreaker.CircuitBreakerError:
        result = ToolResult(tool=call.tool, ok=False, error="circuit_open")
    except Exception as exc:
        result = ToolResult(tool=call.tool, ok=False, error=f"{type(exc).__name__}: {exc}")
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    if key:
        keep = result.ok or not tool.spec.idempotent
        await _idem_set(key, result.model_dump(mode="json") if keep else None)
    await _audit(principal, call, result, thread_id, started)
    return result


async def _guarded(br: pybreaker.CircuitBreaker, tool: Any, principal: Principal, call: ToolCall) -> ToolResult:
    """pybreaker's call_async is Tornado-only, so the awaited outcome is reported to the sync `call`.
    shortcut: an open breaker past reset_timeout is closed by the no-op probe instead of half-open
    gating one real try; swap for aiobreaker if that matters."""
    if br.current_state == pybreaker.STATE_OPEN:
        br.call(lambda: None)  # raises CircuitBreakerError while open
    try:
        async with asyncio.timeout(tool.spec.timeout_seconds):
            outcome: ToolResult | BaseException = await tool.run(principal, call)
    except Exception as exc:
        outcome = exc

    def report() -> ToolResult:
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return br.call(report)


async def _audit(
    principal: Principal, call: ToolCall, result: ToolResult, thread_id: str | None, started: float
) -> None:
    await audit.record(
        "tool.call",
        principal,
        {
            "tool": call.tool,
            "arguments": call.arguments,
            "ok": result.ok,
            "error": result.error,
            "from_cache": result.from_cache,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        },
        thread_id,
    )
