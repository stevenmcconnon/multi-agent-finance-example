"""Shared typed contracts. Every layer imports from here; nobody redefines these.

Layer map (see docs/ARCHITECTURE.md):
  L7 apps/web            L6 agent/ (LangGraph)      L5 harness/ (tools, policy, retries, HITL)
  L4 retrieval/ + authz/ L3 gateway/ (Bedrock/fake) L2 ingest/ (Temporal, EDGAR, parse, embed)
  L1 infra/ (Terraform, OTel)
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ---------- identity (never from the prompt) ----------
class Principal(BaseModel):
    """Authenticated caller. Built ONLY from a verified JWT by harness.identity."""

    user_id: str
    org_id: UUID
    org_slug: str
    groups: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)  # viewer | analyst | admin
    token_issued_at: datetime | None = None


# ---------- policy ----------
class PolicyDecision(BaseModel):
    allow: bool
    action: str  # retrieve | tool:<name> | answer
    resource: str
    reasons: list[str] = Field(default_factory=list)
    policy_ids: list[str] = Field(default_factory=list)
    fail_closed: bool = False  # True when decided by an outage, not a policy


# ---------- documents & retrieval ----------
class Citation(BaseModel):
    document_id: UUID
    chunk_id: UUID | None = None
    title: str | None = None
    uri: str
    section: str | None = None
    version: str | None = None
    snippet: str | None = None


class Chunk(BaseModel):
    id: UUID
    document_id: UUID
    org_id: UUID
    ordinal: int
    section: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None  # fused / rerank score
    uri: str | None = None
    title: str | None = None
    version: str | None = None


class QueryIntent(StrEnum):
    lookup = "lookup"  # textual question over docs
    numeric = "numeric"  # table / value question -> facts + calculator
    compare = "compare"
    out_of_scope = "out_of_scope"


class UnderstoodQuery(BaseModel):
    original: str  # always preserved
    rewritten: str
    intent: QueryIntent
    entities: list[str] = Field(default_factory=list)  # tickers / CIKs
    concepts: list[str] = Field(default_factory=list)  # e.g. Revenues
    period_hint: str | None = None


class RetrievalRequest(BaseModel):
    principal: Principal
    query: UnderstoodQuery
    top_k: int = 24
    rerank_top_n: int = 8
    filters: dict[str, Any] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    chunks: list[Chunk]  # AUTHORIZED candidates only
    candidate_count: int  # before rerank
    acl_filtered: bool = True  # must be True; asserted by tests
    latency_ms: int


class Fact(BaseModel):
    id: UUID
    document_id: UUID
    entity: str
    concept: str
    period_start: date | None
    period_end: date | None
    value: float
    unit: str
    provenance: dict[str, Any]


# ---------- tools ----------
class RiskLevel(StrEnum):
    read = "read"  # no approval
    write = "write"  # HITL approval required
    money = "money"  # HITL approval + second-model verification required


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    risk: RiskLevel = RiskLevel.read
    required_roles: list[str] = Field(default_factory=list)
    idempotent: bool = True
    timeout_seconds: float = 20.0


class ToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any]
    idempotency_key: str | None = None


class ToolResult(BaseModel):
    tool: str
    ok: bool
    output: Any = None
    error: str | None = None
    latency_ms: int = 0
    from_cache: bool = False
    citations: list[Citation] = Field(default_factory=list)


# ---------- model gateway ----------
class ModelRole(StrEnum):
    generate = "generate"
    verify = "verify"
    understand = "understand"


class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    latency_ms: int = 0


# ---------- the turn ----------
class Verification(BaseModel):
    grounded: bool
    confidence: float = Field(ge=0, le=1)
    unsupported_claims: list[str] = Field(default_factory=list)
    verifier_model: str = ""


class Answer(BaseModel):
    text: str
    citations: list[Citation]
    confidence: float = Field(ge=0, le=1)
    abstained: bool = False  # no authorized evidence -> no answer
    verification: Verification | None = None
    thread_id: str
    trace_id: str | None = None


class TurnEvent(BaseModel):
    """SSE event emitted to the UI."""

    type: Literal["status", "policy", "retrieval", "tool", "token", "hitl", "answer", "error"]
    data: dict[str, Any] = Field(default_factory=dict)
