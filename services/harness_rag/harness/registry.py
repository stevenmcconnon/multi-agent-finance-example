"""ToolRegistry: the tools a turn may call. `specs_for` hides tools the principal's roles cannot use;
the runner's Cedar check is still the authority (the list is UX, the policy is enforcement)."""

from __future__ import annotations

from ..contracts import Principal, ToolSpec
from ..interfaces import Tool


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"unknown tool {name!r}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs_for(self, principal: Principal) -> list[ToolSpec]:
        roles = set(principal.roles)
        return [t.spec for t in self._tools.values() if not t.spec.required_roles or roles & set(t.spec.required_roles)]
