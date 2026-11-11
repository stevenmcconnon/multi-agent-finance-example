"""PgFactStore: structured numbers from `facts`, ACL-filtered by the same predicate as chunks.
Numbers are never guessed: the numeric path reads a fact here and computes with calculator.py."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ..contracts import Fact, Principal
from . import sql

Fetch = Callable[[str, dict[str, Any]], Awaitable[list[dict[str, Any]]]]


class PgFactStore:
    def __init__(self, fetch: Fetch | None = None, limit: int = 50) -> None:
        self._fetch = fetch
        self._limit = limit

    @staticmethod
    def facts_query() -> str:
        return sql.FACTS_SQL

    async def _run(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self._fetch is not None:
            return await self._fetch(query, params)
        from .. import db

        return await db.fetch(query, params)

    async def query(self, principal: Principal, entity: str, concept: str, period_end: str | None = None) -> list[Fact]:
        rows = await self._run(
            sql.FACTS_SQL,
            {
                **sql.acl_params(principal),
                "entity": entity,
                "concept": concept,
                "period_end": period_end,
                "k": self._limit,
            },
        )
        return [
            Fact(
                id=r["id"],
                document_id=r["document_id"],
                entity=r["entity"],
                concept=r["concept"],
                period_start=r.get("period_start"),
                period_end=r.get("period_end"),
                value=float(r["value"]),
                unit=r["unit"],
                provenance=r.get("provenance") or {},
            )
            for r in rows
        ]
