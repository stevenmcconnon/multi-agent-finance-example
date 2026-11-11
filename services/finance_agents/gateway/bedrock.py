"""BedrockGateway: bedrock-runtime `converse`. Model per role from settings; structured output by
asking for JSON and parsing the first {...} block; tool selection by Converse native tool use
(`toolConfig` in, `toolUse` content blocks out). Only this module (for models) imports boto3."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ..config import settings
from ..contracts import ModelRole, ModelUsage, ToolCall, ToolSpec, UnderstoodQuery
from ..guardrails.bedrock import converse_guardrail_config
from .fake import extract_json, plan_heuristic, understand_heuristic

log = logging.getLogger(__name__)

UNDERSTAND_SYSTEM = (
    "Classify the user's question about SEC filings. Reply with JSON only: "
    '{"rewritten": str, "intent": "lookup"|"numeric"|"compare"|"out_of_scope", '
    '"entities": [tickers], "concepts": [XBRL concepts such as Revenues], "period_hint": str|null}'
)


PLAN_SYSTEM = (
    "You answer questions about SEC filings using tools. Call the tools needed to gather evidence for the "
    "question (search_documents for text, facts_sql for figures), or a write tool only when the user asks for "
    "that action. If no tool is needed, reply without calling one."
)


def runtime_client() -> Any:
    """The bedrock-runtime client; guardrails/bedrock.py borrows it so boto3 stays behind this door."""
    import boto3

    return boto3.client("bedrock-runtime", region_name=settings.aws_region)


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
        if json_schema is not None:
            system += "\nRespond with JSON only, matching this schema:\n" + json.dumps(json_schema)
        model = self.models[role]
        started = time.perf_counter()
        resp = await asyncio.to_thread(
            self._boto().converse,
            modelId=model,
            system=[{"text": system}],
            messages=[{"role": m["role"], "content": [{"text": str(m["content"])}]} for m in messages],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
            **converse_guardrail_config(),
        )
        text = "".join(part.get("text", "") for part in resp["output"]["message"]["content"])
        u = resp.get("usage", {})
        usage = ModelUsage(
            input_tokens=u.get("inputTokens", 0),
            output_tokens=u.get("outputTokens", 0),
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return text, usage

    async def plan(
        self,
        question: str,
        understood: UnderstoodQuery,
        tools: list[ToolSpec],
        evidence_summary: str | None,
    ) -> list[ToolCall]:
        if not tools:  # converse rejects an empty toolConfig
            return []
        prompt = f"QUESTION: {question}\nUNDERSTOOD: {understood.model_dump_json()}"
        if evidence_summary:
            prompt += f"\nEVIDENCE SO FAR: {evidence_summary}"
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
                inferenceConfig={"maxTokens": 1024, "temperature": 0},
                **converse_guardrail_config(),
            )
        except Exception as exc:  # a planner outage must not kill the turn; the heuristic is the floor
            log.warning("bedrock plan failed, using heuristic: %s", exc)
            return plan_heuristic(question, understood, tools)
        if resp.get("stopReason") == "end_turn":
            return []
        calls = []
        for part in resp["output"]["message"]["content"]:
            use = part.get("toolUse")
            if use and use["name"] in offered:  # a hallucinated tool name is dropped here; Cedar guards the rest
                calls.append(ToolCall(tool=use["name"], arguments=dict(use.get("input") or {})))
        return calls

    async def understand(self, question: str) -> UnderstoodQuery:
        try:
            text, _ = await self.complete(
                ModelRole.understand,
                UNDERSTAND_SYSTEM,
                [{"role": "user", "content": question}],
                max_tokens=300,
            )
            return UnderstoodQuery(original=question, **extract_json(text))
        except Exception as exc:  # a classifier outage must not kill the turn; the heuristic is the floor
            log.warning("bedrock understand failed, using heuristic: %s", exc)
            return understand_heuristic(question)
