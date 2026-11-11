"""Tool planning: the fake heuristic and Bedrock Converse native tool use, both offline."""

from __future__ import annotations

import pytest
from finance_agents.contracts import RiskLevel, ToolSpec
from finance_agents.gateway import FakeGateway
from finance_agents.gateway.bedrock import BedrockGateway
from finance_agents.gateway.fake import understand_heuristic

SEARCH = ToolSpec(name="search_documents", description="search", input_schema={"type": "object"})
FACTS = ToolSpec(name="facts_sql", description="facts", input_schema={"type": "object"})
TICKET = ToolSpec(name="create_ticket", description="ticket", input_schema={"type": "object"}, risk=RiskLevel.write)
ALL = [SEARCH, FACTS, TICKET]


async def _plan(gw, question, tools):
    return await gw.plan(question, understand_heuristic(question), tools, None)


async def test_fake_plans_facts_then_search_for_a_numeric_question():
    calls = await _plan(FakeGateway(), "What was AAPL revenue in 2023?", ALL)
    assert [c.tool for c in calls] == ["facts_sql", "search_documents"]
    assert calls[0].arguments == {"entity": "AAPL", "concept": "Revenues"}


async def test_fake_plans_search_for_a_lookup():
    calls = await _plan(FakeGateway(), "What did AAPL say about net sales?", ALL)
    assert [c.tool for c in calls] == ["search_documents"]
    assert calls[0].arguments["intent"] == "lookup" and calls[0].arguments["query"].startswith("What did")


@pytest.mark.parametrize(
    "question", ["Create a ticket: review ACLs", "please open a ticket review ACLs", "file a ticket - review ACLs"]
)
async def test_fake_plans_create_ticket_on_request(question):
    calls = await _plan(FakeGateway(), question, ALL)
    assert [c.tool for c in calls] == ["create_ticket"] and calls[0].arguments == {"title": "review ACLs"}


async def test_fake_never_plans_a_tool_it_was_not_offered():
    assert await _plan(FakeGateway(), "Create a ticket: review ACLs", [SEARCH, FACTS]) == []
    calls = await _plan(FakeGateway(), "What was AAPL revenue in 2023?", [SEARCH])
    assert [c.tool for c in calls] == ["search_documents"]


class _Converse:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    def converse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


async def test_bedrock_parses_tool_use_blocks():
    client = _Converse(
        {
            "stopReason": "tool_use",
            "output": {
                "message": {
                    "content": [
                        {"text": "Let me look that up."},
                        {
                            "toolUse": {
                                "toolUseId": "1",
                                "name": "facts_sql",
                                "input": {"entity": "AAPL", "concept": "Revenues"},
                            }
                        },
                        {"toolUse": {"toolUseId": "2", "name": "search_documents", "input": {"query": "AAPL revenue"}}},
                        {"toolUse": {"toolUseId": "3", "name": "delete_everything", "input": {}}},
                    ]
                }
            },
        }
    )
    calls = await _plan(BedrockGateway(client=client), "What was AAPL revenue in 2023?", ALL)
    assert [(c.tool, c.arguments) for c in calls] == [
        ("facts_sql", {"entity": "AAPL", "concept": "Revenues"}),
        ("search_documents", {"query": "AAPL revenue"}),
    ]
    sent = client.kwargs["toolConfig"]["tools"]
    assert [t["toolSpec"]["name"] for t in sent] == ["search_documents", "facts_sql", "create_ticket"]
    assert sent[0]["toolSpec"]["inputSchema"] == {"json": {"type": "object"}}


async def test_bedrock_end_turn_without_tool_use_plans_nothing():
    client = _Converse({"stopReason": "end_turn", "output": {"message": {"content": [{"text": "No tool needed."}]}}})
    assert await _plan(BedrockGateway(client=client), "hello?", ALL) == []
    assert await _plan(BedrockGateway(client=client), "hello?", []) == []  # no tools offered -> no model call
