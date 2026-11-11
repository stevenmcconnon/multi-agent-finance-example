"""Form -> reader groups (docs/CONTRACTS.md), resolved to principal ids in the org."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

Fetch = Callable[[str, Any], Awaitable[list[dict[str, Any]]]]

FORM_GROUPS = {
    "10-K": ("finance", "analysts"),
    "10-Q": ("finance", "analysts"),
    "8-K": ("engineering", "finance", "analysts"),
}
DEFAULT_GROUPS = ("finance", "analysts")  # any other form reads like a periodic report


def groups_for(form: str) -> tuple[str, ...]:
    return FORM_GROUPS.get(form.split("/")[0], DEFAULT_GROUPS)  # 10-K/A reads like 10-K


async def principal_ids(org_id: UUID | str, form: str, fetch: Fetch | None = None) -> list[UUID]:
    if fetch is None:
        from .. import db

        fetch = db.fetch
    rows = await fetch(
        "SELECT id FROM principals WHERE org_id = %s AND kind = 'group' AND external_id = ANY(%s)",
        (str(org_id), list(groups_for(form))),
    )
    return [r["id"] for r in rows]
