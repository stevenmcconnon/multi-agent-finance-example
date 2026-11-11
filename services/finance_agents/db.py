"""Postgres door: one psycopg3 async pool, pgvector registered, migrations applied in order.

Every layer that touches Postgres goes through `pool()`, `fetch()` or `execute()`.
Row-level authorization is NOT here: it is the ACL predicate in retrieval/sql.py, applied
by the retriever and fact store. This module only knows how to connect.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from .config import settings

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

_pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
_pool_lock = asyncio.Lock()


async def _configure(conn: AsyncConnection[DictRow]) -> None:
    """Runs once per new pooled connection: pgvector adapters + dict rows."""
    try:
        await register_vector_async(conn)
    except Exception as exc:  # the extension is created by 0001; before that, vectors are unused
        log.warning("pgvector not registered on connection: %s", exc)
    conn.row_factory = dict_row


async def pool(dsn: str | None = None) -> AsyncConnectionPool[AsyncConnection[DictRow]]:
    """The process-wide pool (lazily opened). `dsn` overrides settings.database_url (tests)."""
    global _pool
    async with _pool_lock:
        if _pool is None:
            _pool = AsyncConnectionPool(
                conninfo=dsn or settings.database_url,
                connection_class=AsyncConnection[DictRow],
                min_size=1,
                max_size=10,
                open=False,
                configure=_configure,
                kwargs={"autocommit": True},
            )
            await _pool.open(wait=True, timeout=10)
        return _pool


async def close_pool() -> None:
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
            _pool = None


async def fetch(
    sql: str, params: Sequence[Any] | dict[str, Any] | None = None, *, iterative_scan: bool = False
) -> list[dict[str, Any]]:
    """Run a read query; rows come back as dicts.

    `iterative_scan`: keep scanning the HNSW index until LIMIT rows pass the WHERE filter. Without it
    pgvector filters AFTER taking hnsw.ef_search (40) nearest rows, so a user who may read few chunks
    (the ACL predicate) gets almost no dense rows. SET LOCAL needs a transaction (the pool autocommits).
    """
    p = await pool()
    async with p.connection() as conn, conn.transaction(), conn.cursor() as cur:
        if iterative_scan:
            await cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        await cur.execute(sql, params)
        if cur.description is None:
            return []
        return list(await cur.fetchall())


async def execute(sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> int:
    """Run a statement; returns the affected row count (-1 when unknown)."""
    p = await pool()
    async with p.connection() as conn, conn.cursor() as cur:
        await cur.execute(sql, params)
        return cur.rowcount


_MIGRATION_NAME = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """db/migrations/*.sql in lexical (= numeric prefix) order."""
    return sorted(p for p in directory.glob("*.sql") if _MIGRATION_NAME.match(p.name))


async def run_migrations(directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every unapplied migration in order inside its own transaction.

    Applied files are recorded in `schema_migrations(filename)`; re-running is a no-op.
    Returns the filenames applied this run.
    """
    p = await pool()
    applied: list[str] = []
    async with p.connection() as conn, conn.transaction():
        # Serialize concurrent migrators (two API replicas booting at once) BEFORE the first write.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('finance_agents.schema_migrations'))")
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  filename text PRIMARY KEY,"
            "  applied_at timestamptz NOT NULL DEFAULT now()"
            ")"
        )
        cur = await conn.execute("SELECT filename FROM schema_migrations")
        done = {r["filename"] for r in await cur.fetchall()}
        for path in migration_files(directory):
            if path.name in done:
                continue
            log.info("applying migration %s", path.name)
            await conn.execute(path.read_text())
            await conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            applied.append(path.name)
    # pgvector adapters may have failed to register on connections opened before 0001 created the extension.
    if applied:
        await p.check()
    return applied


async def ping(dsn: str | None = None, timeout: float = 2.0) -> bool:
    """True when the database answers; used by tests to skip DB-backed cases."""
    try:
        conn = await asyncio.wait_for(AsyncConnection.connect(dsn or settings.database_url), timeout)
    except Exception:
        return False
    try:
        await conn.execute("SELECT 1")
        return True
    except Exception:
        return False
    finally:
        await conn.close()
