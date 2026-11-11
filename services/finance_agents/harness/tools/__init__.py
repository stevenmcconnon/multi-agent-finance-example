"""The built-in tools. `default_registry()` wires them to the configured retriever and fact store."""

from __future__ import annotations

from ..registry import ToolRegistry
from .calculator import CalculatorTool
from .create_ticket import CreateTicketTool
from .facts_sql import FactsSqlTool
from .search_documents import SearchDocumentsTool

__all__ = ["CalculatorTool", "CreateTicketTool", "FactsSqlTool", "SearchDocumentsTool", "default_registry"]


def default_registry() -> ToolRegistry:
    from ...retrieval import get_fact_store, get_retriever

    return ToolRegistry(
        [SearchDocumentsTool(get_retriever()), FactsSqlTool(get_fact_store()), CalculatorTool(), CreateTicketTool()]
    )
