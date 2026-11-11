"""Deterministic input checks. PII (personally identifiable information) is redacted, never blocked;
a prompt-injection match is TAGGED, never blocked (Cedar and the tool registry already make it harmless,
and blocking would hide the evidence from the audit row). Only hard failures block.

In production: a managed classifier (Bedrock Guardrails PROMPT_ATTACK, PII entity detection) runs
  alongside these patterns; see guardrails/bedrock.py.
In this demo: regex lists only, because they run offline and are enough to show the shape.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

MAX_LEN = 2000  # the same cap as api.app.ChatBody

# Order matters: an e-mail may contain digits, a card run may contain a phone-shaped run.
_PII: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("card", re.compile(r"\b(?:\d[ -]?){12,15}\d\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(r"(?:\+?1[-.\s]?)?\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")),
]
_INJECTION = re.compile(
    r"ignore (?:all |the |any )?(?:previous|prior|above) (?:instructions|rules|prompts?)"
    r"|disregard (?:your|the|all) (?:rules|instructions|guidelines)"
    r"|reveal (?:your |the )?system prompt"
    r"|(?:print|show|repeat) (?:your |the )?(?:system )?(?:prompt|instructions)"
    r"|act as (?:an? |the )?(?:admin|administrator|root|developer|system)"
    r"|you are now (?:an? |the )?(?:admin|administrator|root|developer|unrestricted)"
    r"|show (?:me )?(?:the )?documents? (?:you|i) (?:may|can|are) not"
    r"|(?:ignore|bypass|disregard|override|circumvent) (?:(?:your|the|my|all|any|these|those) )*"
    r"(?:acls?|permissions?|access controls?|polic(?:y|ies)|rules|restrictions|filters?)"
    r"|show (?:me )?[^.?!]{0,80}?(?:you|i) (?:are|am|\'re|\'m) not (?:allowed|permitted|authorized)"
    r"|\bas an? admin(?:istrator)?\b"
    r"|jailbreak|DAN mode",
    re.IGNORECASE,
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class InputVerdict(BaseModel):
    ok: bool
    reasons: list[str] = Field(default_factory=list)
    redacted_question: str = ""
    injection_suspected: bool = False
    redactions: list[str] = Field(default_factory=list)


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0


def redact(text: str) -> tuple[str, list[str]]:
    """Replace each PII match with `[redacted-<kind>]`; returns the text and the kinds found, in order."""
    kinds: list[str] = []
    for kind, pat in _PII:

        def _sub(m: re.Match[str], kind: str = kind) -> str:
            if kind == "card" and not _luhn(re.sub(r"\D", "", m.group(0))):
                return m.group(0)  # a fiscal figure, not a card
            kinds.append(kind)
            return f"[redacted-{kind}]"

        text = pat.sub(_sub, text)
    return text, kinds


def check_input(question: str) -> InputVerdict:
    reasons: list[str] = []
    if len(question) > MAX_LEN:
        reasons.append("too_long")
    if _CONTROL.search(question):
        reasons.append("control_characters")
    redacted, kinds = redact(question)
    if not redacted.strip() or not re.sub(r"\[redacted-\w+\]", "", redacted).strip():
        reasons.append("empty_after_redaction")
    return InputVerdict(
        ok=not reasons,
        reasons=reasons,
        redacted_question=redacted,
        injection_suspected=bool(_INJECTION.search(question)),
        redactions=kinds,
    )
