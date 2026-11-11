"""Deterministic output checks on the final answer: PII redaction, and `uncited` when an ANSWER (reason
`answered`) carries prose but no citation. An abstention or an action outcome has nothing to cite and is never
flagged. The Judge (agent/graph.py verify) normally prevents an uncited answer; this is the belt to its braces
and only flags, the answer still streams."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .input import redact


class OutputVerdict(BaseModel):
    text: str
    redactions: list[str] = Field(default_factory=list)
    uncited: bool = False


def check_output(text: str, citations_count: int, reason: str = "answered") -> OutputVerdict:
    redacted, kinds = redact(text)
    claims = bool(redacted.strip()) and reason == "answered"
    return OutputVerdict(text=redacted, redactions=kinds, uncited=claims and citations_count == 0)
