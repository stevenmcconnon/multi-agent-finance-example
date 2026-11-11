"""Amazon Bedrock Guardrails door. Configured by BEDROCK_GUARDRAIL_ID (+ _VERSION); off when empty.

Two uses: `converse_guardrail_config()` is spliced into every Converse call by gateway/bedrock.py, and
`BedrockGuardrail.apply()` runs the standalone ApplyGuardrail check on the user's question (INPUT) and
the final answer (OUTPUT) from api/app.py.

In production: the guardrail in infra/terraform/modules/guardrails (content filters, PII anonymized,
  an "investment advice" denied topic) runs on every model call and on both ends of the turn.
In this demo: HR_MODEL_BACKEND=fake and no guardrail id, so only guardrails/input.py and output.py run,
  because the compose stack has no AWS and this path was never exercised live in this repo.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import settings

log = logging.getLogger(__name__)


def converse_guardrail_config() -> dict[str, Any]:
    """`{"guardrailConfig": {...}}` for Converse, or `{}` when no guardrail is configured."""
    if not settings.bedrock_guardrail_id:
        return {}
    return {
        "guardrailConfig": {
            "guardrailIdentifier": settings.bedrock_guardrail_id,
            "guardrailVersion": settings.bedrock_guardrail_version,
        }
    }


class GuardrailOutcome(BaseModel):
    blocked: bool  # GUARDRAIL_INTERVENED: the outputs carry the blocked message or the anonymized text
    outputs: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str | None:
        return self.outputs[0] if self.outputs else None


class BedrockGuardrail:
    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(settings.bedrock_guardrail_id)

    def _boto(self) -> Any:
        if self._client is None:
            from ..gateway.bedrock import runtime_client  # the one module that imports boto3

            self._client = runtime_client()
        return self._client

    async def apply(self, text: str, source: Literal["INPUT", "OUTPUT"] = "INPUT") -> GuardrailOutcome:
        resp = await asyncio.to_thread(
            self._boto().apply_guardrail,
            guardrailIdentifier=settings.bedrock_guardrail_id,
            guardrailVersion=settings.bedrock_guardrail_version,
            source=source,
            content=[{"text": {"text": text}}],
        )
        return GuardrailOutcome(
            blocked=resp.get("action") == "GUARDRAIL_INTERVENED",
            outputs=[o["text"] for o in resp.get("outputs", []) if "text" in o],
        )
