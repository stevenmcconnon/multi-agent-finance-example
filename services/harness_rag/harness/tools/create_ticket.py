from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Field

from ...authz import audit
from ...contracts import Principal, RiskLevel, ToolCall, ToolResult, ToolSpec


class TicketInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = ""


class CreateTicketTool:
    """The HITL demo write: the "ticket" is an audit row. shortcut: no ticketing backend, swap the
    body for a Jira/ServiceNow client when one exists; the approval path stays the same."""

    spec = ToolSpec(
        name="create_ticket",
        description="Open a follow-up ticket (requires human approval).",
        input_schema=TicketInput.model_json_schema(),
        risk=RiskLevel.write,
        required_roles=["admin"],
        idempotent=False,
    )

    async def run(self, principal: Principal, call: ToolCall) -> ToolResult:
        inp = TicketInput(**call.arguments)
        ticket_id = f"TCK-{uuid4().hex[:8].upper()}"
        await audit.record("hitl", principal, {"event": "ticket.created", "ticket_id": ticket_id, **inp.model_dump()})
        return ToolResult(tool=self.spec.name, ok=True, output={"ticket_id": ticket_id, "title": inp.title})
