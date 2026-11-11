"""BedrockGateway: bedrock-runtime `converse`. Model per role from settings; structured output by
asking for JSON and parsing the first {...} block; tool selection by Converse native tool use
(`toolConfig` in, `toolUse` content blocks out). Only this module (for models) imports boto3.

The models (docs/CONTRACTS.md "Bedrock models"): inference profile ids, no `temperature`/`top_p` (these models
answer 400 to them), thinking always on. Thinking spends output tokens, so every call's `maxTokens` leaves room
for it, and replies are read from `text` parts only (a reply may open with `reasoningContent` parts).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ..config import settings
from ..contracts import ModelRole, ModelUsage, ToolCall, ToolSpec, UnderstoodQuery
from ..guardrails.bedrock import converse_guardrail_config
from ..interfaces import ModelCallFailed, ModelRefused
from . import note
from .fake import extract_json, plan_heuristic, understand_heuristic

log = logging.getLogger(__name__)

UNDERSTAND_SYSTEM = (
    "Classify the user's message about SEC filings. Reply with JSON only: "
    '{"rewritten": str, "intent": "lookup"|"numeric"|"compare"|"action"|"out_of_scope", '
    '"entities": [tickers], "concepts": [XBRL concepts such as Revenues], "period_hint": str|null}. '
    '"action" is a request to do something (for example create a ticket) rather than a question. '
    '"period_hint" is the four-digit fiscal years named, comma-separated (for example "2024,2026").'
)


PLAN_SYSTEM = (
    "You answer questions about SEC filings using tools. Call the tools needed to gather evidence for the "
    "question (search_documents for text, facts_sql for figures), or a write tool only when the user asks for "
    "that action. If no tool is needed, reply without calling one."
)

# Output caps per call. Thinking is always on and is billed as output, so a cap sized for the visible reply
# alone would be spent before the first word of it.
MAX_TOKENS = {ModelRole.generate: 8000, ModelRole.verify: 4000, ModelRole.understand: 2000}
PLAN_MAX_TOKENS = 4000
REFUSAL_STOP_REASONS = {"refusal", "content_filtered", "guardrail_intervened"}


def runtime_client() -> Any:
    """The bedrock-runtime client; guardrails/bedrock.py borrows it so boto3 stays behind this door."""
    import boto3

    return boto3.client("bedrock-runtime", region_name=settings.aws_region)


def error_code(exc: BaseException) -> str:
    """The AWS error code of a botocore ClientError (AccessDeniedException, ValidationException,
    ThrottlingException, ...); for anything else the exception's type name."""
    if isinstance(exc, ModelCallFailed):
        return exc.code
    response = getattr(exc, "response", None)
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return str(code or type(exc).__name__)


class BedrockGateway:
    def __init__(self, client: Any | None = None) -> None:
        self._client = client
        self.models = {
            ModelRole.generate: settings.bedrock_generation_model,
            ModelRole.verify: settings.bedrock_verifier_model,
            ModelRole.understand: settings.bedrock_verifier_model,  # cheap model classifies
        }

    def _boto(self) -> Any:
        if self._client is None:
            self._client = runtime_client()
        return self._client

    async def complete(
        self,
        role: ModelRole,
        system: str,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        json_schema: dict | None = None,
    ) -> tuple[str, ModelUsage]:
        """Raises ModelCallFailed (the message names the AWS error code and the model id) when the call fails
        and ModelRefused when the model declines; never returns a stand-in answer."""
        if json_schema is not None:
            system += "\nRespond with JSON only, matching this schema:\n" + json.dumps(json_schema)
        model = self.models[role]
        started = time.perf_counter()
        try:
            resp = await asyncio.to_thread(
                self._boto().converse,
                modelId=model,
                system=[{"text": system}],
                messages=[{"role": m["role"], "content": [{"text": str(m["content"])}]} for m in messages],
                inferenceConfig={"maxTokens": max(max_tokens, MAX_TOKENS[role])},
                **converse_guardrail_config(),
            )
        except Exception as exc:
            code = error_code(exc)
            raise ModelCallFailed(f"Bedrock {role.value} call failed ({code}) for model {model}: {exc}", code) from exc
        if resp.get("stopReason") in REFUSAL_STOP_REASONS:
            raise ModelRefused(f"{model} declined to answer (stopReason {resp['stopReason']})")
        text = "".join(part["text"] for part in resp["output"]["message"]["content"] if "text" in part)
        u = resp.get("usage", {})
        usage = ModelUsage(
            input_tokens=u.get("inputTokens", 0),
            output_tokens=u.get("outputTokens", 0),
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return text, usage

    async def plan(self, question: str, understood: UnderstoodQuery, tools: list[ToolSpec]) -> list[ToolCall]:
        if not tools:  # converse rejects an empty toolConfig
            return []
        prompt = f"QUESTION: {question}\nUNDERSTOOD: {understood.model_dump_json()}"
        offered = {t.name for t in tools}
        try:
            resp = await asyncio.to_thread(
                self._boto().converse,
                modelId=self.models[ModelRole.generate],
                system=[{"text": PLAN_SYSTEM}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                toolConfig={
                    "tools": [
                        {
                            "toolSpec": {
                                "name": t.name,
                                "description": t.description,
                                "inputSchema": {"json": t.input_schema},
                            }
                        }
                        for t in tools
                    ]
                },
                inferenceConfig={"maxTokens": PLAN_MAX_TOKENS},
                **converse_guardrail_config(),
            )
        except Exception as exc:  # a planner outage must not kill the turn; the heuristic is the floor, and says so
            log.warning("bedrock plan failed, using heuristic: %s", exc)
            note(f"planner call failed ({error_code(exc)}); used the deterministic fallback")
            return plan_heuristic(question, understood, tools)
        # toolUse parts wherever they sit (after text or reasoningContent parts); none means no calls,
        # whatever stopReason says.
        calls = []
        for part in resp["output"]["message"]["content"]:
            use = part.get("toolUse")
            if use and use["name"] in offered:  # a hallucinated tool name is dropped here; Cedar guards the rest
                calls.append(ToolCall(tool=use["name"], arguments=dict(use.get("input") or {})))
        return calls

    async def understand(self, question: str) -> UnderstoodQuery:
        try:
            text, _ = await self.complete(
                ModelRole.understand, UNDERSTAND_SYSTEM, [{"role": "user", "content": question}]
            )
            return UnderstoodQuery(original=question, **extract_json(text))
        except Exception as exc:  # a classifier outage must not kill the turn; the heuristic is the floor, and says so
            log.warning("bedrock understand failed, using heuristic: %s", exc)
            note(f"classifier call failed ({error_code(exc)}); used the deterministic fallback")
            return understand_heuristic(question)
