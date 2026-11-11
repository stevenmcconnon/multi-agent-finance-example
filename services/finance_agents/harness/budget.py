"""TurnBudget: tokens, tool calls and a wall-clock deadline per turn, from settings."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import settings


class BudgetExceeded(Exception):
    pass


@dataclass
class TurnBudget:
    tokens: int = field(default_factory=lambda: settings.hr_turn_token_budget)
    tool_calls: int = field(default_factory=lambda: settings.hr_turn_tool_call_budget)
    deadline: float = field(default_factory=lambda: time.monotonic() + settings.hr_turn_timeout_seconds)
    tokens_used: int = 0
    tool_calls_used: int = 0

    def charge(self, *, tokens: int = 0, tool_calls: int = 0) -> None:
        self.tokens_used += tokens
        self.tool_calls_used += tool_calls
        if time.monotonic() > self.deadline:
            raise BudgetExceeded("turn deadline passed")
        if self.tokens_used > self.tokens:
            raise BudgetExceeded(f"token budget {self.tokens} exceeded ({self.tokens_used})")
        if self.tool_calls_used > self.tool_calls:
            raise BudgetExceeded(f"tool call budget {self.tool_calls} exceeded ({self.tool_calls_used})")
