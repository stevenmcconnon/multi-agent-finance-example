"""ingest_jobs: one row per (org, source, source_id, version); attempts counted; dead after MAX_ATTEMPTS."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

Fetch = Callable[[str, Any], Awaitable[list[dict[str, Any]]]]
MAX_ATTEMPTS = 5
TERMINAL = {"succeeded", "dead"}

log = structlog.get_logger(__name__)


class JobDead(Exception):
    """Raised once a job has failed MAX_ATTEMPTS times and is recorded dead (ingest_jobs.status='dead')."""


def idempotency_key(org_id: str, source: str, source_id: str, version: str) -> str:
    return hashlib.sha256("|".join((str(org_id), source, source_id, version)).encode()).hexdigest()


async def _fetch(fetch: Fetch | None) -> Fetch:
    if fetch is not None:
        return fetch
    from .. import db

    return db.fetch


async def claim(msg: dict[str, Any], fetch: Fetch | None = None) -> dict[str, Any] | None:
    """Insert the job as queued (or return the existing row). None when it already reached a terminal state."""
    fetch = await _fetch(fetch)
    key = idempotency_key(msg["org_id"], msg["source"], msg["source_id"], msg["version"])
    rows = await fetch(
        "INSERT INTO ingest_jobs (org_id, source, source_id, version, idempotency_key, status) "
        "VALUES (%s, %s, %s, %s, %s, 'queued') "
        "ON CONFLICT (idempotency_key) DO UPDATE SET updated_at = now() "
        "RETURNING id, status, attempts",
        (str(msg["org_id"]), msg["source"], msg["source_id"], msg["version"], key),
    )
    row = rows[0]
    return None if row["status"] in TERMINAL else row


async def mark(
    job_id: Any,
    status: str,
    error: str | None = None,
    fetch: Fetch | None = None,
    attempts: int | None = None,
) -> str:
    """Set status (+`attempts` on 'failed', default 1); the 5th failure becomes 'dead'. Returns the stored status."""
    fetch = await _fetch(fetch)
    inc = (1 if attempts is None else attempts) if status == "failed" else 0
    rows = await fetch(
        "UPDATE ingest_jobs SET status = %s, error = %s, attempts = attempts + %s, updated_at = now() "
        "WHERE id = %s RETURNING attempts",
        (status, error, inc, job_id),
    )
    if status == "failed" and rows[0]["attempts"] >= MAX_ATTEMPTS:
        await fetch("UPDATE ingest_jobs SET status = 'dead', updated_at = now() WHERE id = %s RETURNING id", (job_id,))
        return "dead"
    return status


async def run(msg: dict[str, Any], work: Callable[[dict[str, Any]], Awaitable[Any]], fetch: Fetch | None = None) -> str:
    """One attempt: claim -> running -> work -> succeeded | failed (re-raises) | dead (JobDead)."""
    job = await claim(msg, fetch)
    if job is None:
        log.info("ingest.skip", source_id=msg["source_id"], version=msg["version"])
        return "skipped"
    await mark(job["id"], "running", fetch=fetch)
    try:
        await work(msg)
    except Exception as exc:
        status = await mark(job["id"], "failed", str(exc)[:2000], fetch=fetch)
        log.warning("ingest.failed", source_id=msg["source_id"], status=status, error=str(exc))
        if status == "dead":
            raise JobDead(str(exc)) from exc
        raise
    return await mark(job["id"], "succeeded", fetch=fetch)
