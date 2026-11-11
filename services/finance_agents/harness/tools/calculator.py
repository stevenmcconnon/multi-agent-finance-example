from __future__ import annotations

from pydantic import BaseModel, Field

from ...contracts import Principal, RiskLevel, ToolCall, ToolResult, ToolSpec
from ...retrieval.calculator import CalculatorError, evaluate


class CalcInput(BaseModel):
    expression: str = Field(min_length=1, max_length=500)


class CalculatorTool:
    spec = ToolSpec(
        name="calculator",
        description="Safe arithmetic: numbers, + - * / % **, parentheses.",
        input_schema=CalcInput.model_json_schema(),
        risk=RiskLevel.read,
        required_roles=["analyst", "admin"],
        timeout_seconds=2.0,
    )

    async def run(self, principal: Principal, call: ToolCall) -> ToolResult:
        try:
            return ToolResult(
                tool=self.spec.name,
                ok=True,
                output={"value": evaluate(CalcInput(**call.arguments).expression)},
            )
        except CalculatorError as exc:
            return ToolResult(tool=self.spec.name, ok=False, error=str(exc))
