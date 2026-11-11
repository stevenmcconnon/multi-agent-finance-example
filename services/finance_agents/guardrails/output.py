"""Deterministic output checks on the final answer: PII redaction, and `uncited` when prose carries no
citation and is not the abstention. The Judge (agent/graph.py verify) normally prevents an uncited answer;
this is the belt to its braces and only flags, the answer still streams."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..gateway.fake import ABSTAIN_TEXT
from .input import redact


class OutputVerdict(BaseModel):
    text: str
    redactions: list[str] = Field(default_factory=list)
    uncited: bool = False


def check_output(text: str, citations_count: int) -> OutputVerdict:
    redacted, kinds = redact(text)
    claims = bool(redacted.strip()) and redacted.strip() != ABSTAIN_TEXT
    return OutputVerdict(text=redacted, redactions=kinds, uncited=claims and citations_count == 0)
