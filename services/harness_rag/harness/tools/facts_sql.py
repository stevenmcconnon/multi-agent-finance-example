from __future__ import annotations

from pydantic import BaseModel, Field

from ...contracts import Citation, Principal, RiskLevel, ToolCall, ToolResult, ToolSpec
from ...interfaces import FactStore


class FactsInput(BaseModel):
    entity: str = Field(min_length=1, description="ticker or CIK")
    concept: str = Field(min_length=1, description="XBRL concept, e.g. Revenues")
    period_end: str | None = Field(default=None, description="YYYY-MM-DD")


class FactsSqlTool:
    spec = ToolSpec(
        name="facts_sql",
        description="Structured XBRL facts (entity, concept, period) from documents the caller may read.",
        input_schema=FactsInput.model_json_schema(),
        risk=RiskLevel.read,
        required_roles=["analyst", "admin"],
    )

    def __init__(self, store: FactStore) -> None:
        self._store = store

    async def run(self, principal: Principal, call: ToolCall) -> ToolResult:
        inp = FactsInput(**call.arguments)
        facts = await self._store.query(principal, inp.entity, inp.concept, inp.period_end)
        return ToolResult(
            tool=self.spec.name,
            ok=True,
            output={"facts": [f.model_dump(mode="json") for f in facts]},
            citations=[
                Citation(
                    document_id=f.document_id,
                    title=str(f.provenance.get("title", "")) or None,
                    uri=str(f.provenance.get("uri", "")),
                    section=f"XBRL fact {f.concept}",
                    version=str(f.provenance.get("version", "")) or None,
                    snippet=str(f.provenance.get("raw_text", ""))[:200],
                )
                for f in facts
            ],
        )
