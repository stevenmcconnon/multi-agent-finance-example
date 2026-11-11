"""The model gateway offline: the deterministic stub (understand, plan, extractive generate) and the Bedrock
adapter against a stubbed bedrock-runtime client (nothing here can spend)."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError
from finance_agents.config import Settings
from finance_agents.contracts import ModelRole, QueryIntent, RiskLevel, ToolSpec
from finance_agents.gateway import ABSTAIN_TEXT, FakeGateway, format_evidence, take_notes
from finance_agents.gateway.bedrock import BedrockGateway
from finance_agents.gateway.fake import understand_heuristic
from finance_agents.interfaces import ModelCallFailed, ModelRefused

SEARCH = ToolSpec(name="search_documents", description="search", input_schema={"type": "object"})
FACTS = ToolSpec(name="facts_sql", description="facts", input_schema={"type": "object"})
TICKET = ToolSpec(name="create_ticket", description="ticket", input_schema={"type": "object"}, risk=RiskLevel.write)
ALL = [SEARCH, FACTS, TICKET]


async def _plan(gw, question, tools):
    return await gw.plan(question, understand_heuristic(question), tools)


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


# ---------- the offline stub ----------
@pytest.mark.parametrize(
    "question, intent, entities, concepts, period",
    [
        ("What risks does Apple describe about its supply chain?", "lookup", ["AAPL"], [], None),
        ("What was Apple's revenue in fiscal 2026?", "numeric", ["AAPL"], ["Revenues"], "2026"),
        (
            "How did Apple's net income change from fiscal 2024 to fiscal 2026?",
            "compare",
            ["AAPL"],
            ["NetIncomeLoss"],
            "2024,2026",
        ),
        ("Which Microsoft director is not standing for re-election?", "lookup", ["MSFT"], [], None),
        ("Create a ticket to review Apple's 10-K risk section", "action", [], [], None),
        ("please open a ticket about the audit", "action", [], [], None),
        ("What were microsoft's earnings per share in 2025?", "numeric", ["MSFT"], ["EarningsPerShareDiluted"], "2025"),
        (
            "How much cash and total assets did NVIDIA report?",
            "numeric",
            ["NVDA"],
            ["Assets", "CashAndCashEquivalentsAtCarryingValue"],
            None,
        ),
        ("What does Amazon say about profit growth?", "compare", ["AMZN"], ["NetIncomeLoss"], None),
        ("Write me a poem about autumn.", "out_of_scope", [], [], None),
        # out_of_scope is the exception: empty, small talk, under two content words, or nothing like a request
        ("", "out_of_scope", [], [], None),
        ("hello", "out_of_scope", [], [], None),
        ("Thanks, that is great!", "out_of_scope", [], [], None),
        ("Revenue?", "out_of_scope", [], [], None),
        ("Apple rocks", "out_of_scope", [], [], None),
        ("What is the weather today?", "lookup", [], [], None),
        # imperatives about the filings are in scope
        ("Compare Apple and Microsoft revenue", "compare", ["AAPL", "MSFT"], ["Revenues"], None),
        ("Summarize Apple's risk factors", "lookup", ["AAPL"], [], None),
        ("Tell me about Microsoft's board", "lookup", ["MSFT"], [], None),
        # known tickers in any case, as whole words; an abbreviation is not a ticker
        ("what was aapl revenue in 2025", "numeric", ["AAPL"], ["Revenues"], "2025"),
        ("aapl revenue 2025", "numeric", ["AAPL"], ["Revenues"], "2025"),
        ("What does the metadata say about revenue?", "lookup", [], ["Revenues"], None),
        ("Who is Apple's CFO?", "lookup", ["AAPL"], [], None),
        # per share / EPS is its own concept; earnings alone is net income
        ("What did Apple earn per share in fiscal 2026?", "numeric", ["AAPL"], ["EarningsPerShareDiluted"], "2026"),
        ("What is Microsoft's EPS?", "numeric", ["MSFT"], ["EarningsPerShareDiluted"], None),
        ("What were Apple's earnings in 2026?", "numeric", ["AAPL"], ["NetIncomeLoss"], "2026"),
        ("How much did Apple earn in 2026?", "numeric", ["AAPL"], ["NetIncomeLoss"], "2026"),
        ("What did AAPL say about net sales?", "lookup", ["AAPL"], ["Revenues"], None),
        # comparison words make it a compare
        ("How much did Apple's revenue increase?", "compare", ["AAPL"], ["Revenues"], None),
        ("What was Apple's revenue compared to 2024?", "compare", ["AAPL"], ["Revenues"], "2024"),
    ],
)
def test_understand_heuristic(question, intent, entities, concepts, period):
    u = understand_heuristic(question)
    assert (u.intent, u.entities, u.concepts, u.period_hint) == (QueryIntent(intent), entities, concepts, period)


async def _write(question: str, evidence: list[str]) -> str:
    prompt = f"QUESTION: {question}\n\nEVIDENCE:\n{format_evidence(evidence)}"
    text, usage = await FakeGateway().complete(ModelRole.generate, "sys", [{"role": "user", "content": prompt}])
    assert usage.model == "offline-stub"
    return text


async def test_stub_writer_picks_the_relevant_sentence_and_skips_fragments():
    relevant = "The Company depends on single-source suppliers, which exposes its supply chain to disruption."
    chunk = f"and other matters described below. Item 1A covers many topics in general terms only. {relevant}"
    fact = (
        "AAPL revenue for the fiscal year ended 2026-09-26: "
        "$478.83 billion (USD 478,834,000,000; XBRL concept Revenues)."
    )
    text = await _write(
        "What risks does Apple describe about its supply chain?",
        [
            "Indicate by check mark whether the registrant has supply chain risks described ☐ Yes ☒ No today.",
            chunk,
            "Unrelated remarks about the weather in Cupertino on a sunny afternoon in late spring.",
            fact,
            "Supply risks.",
        ],
    )
    assert text == f"{fact} [4] {relevant} [2]"  # the structured fact first, then the best sentence, verbatim
    assert await _write("Which director plans to retire?", [chunk]) == ABSTAIN_TEXT


async def test_stub_writer_keeps_at_most_five_sentences_and_drops_weak_matches():
    weak = [f"Paragraph {i} mentions the supply chain in passing and nothing else at all today." for i in range(6)]
    strong = "Supply chain risks exist because of reliance on outsourcing partners located in a few regions."
    one = ["A sentence that mentions the supply of parts and shares only that single word with it."]
    q = "What supply chain risks exist in outsourcing?"
    assert (await _write(q, weak)).count("[") == 5  # equal scores: the first five
    assert await _write(q, [*weak, strong]) == f"{strong} [7]"  # two shared words beside five is noise
    assert await _write(q, one) == ABSTAIN_TEXT  # one shared word never qualifies when the question has two


EXHIBITS = (
    "Exhibits. 2 Exhibit Number Exhibit Description 1.1 Underwriting Agreement, dated November 5, 2026, among Apple "
    "Inc. and the several underwriters named therein 4.1 Officer's Certificate of Apple Inc., dated November 9, 2026."
)
CLOUD = (
    "These amounts are included in Server products and cloud services, Microsoft 365 Commercial products and cloud "
    "services, LinkedIn, and Dynamics products and cloud services in the table above."
)


@pytest.mark.parametrize(
    "question, evidence",
    [
        ("Who is Apple's CFO?", EXHIBITS),  # shared only "Apple": an entity word says whose filing, not what
        ("What notes did Apple issue in November 2026?", EXHIBITS),  # an exhibit index is not a sentence
        ("What is Microsoft's dividend?", CLOUD),  # shared only "Microsoft"
        ("What is Microsoft's dividend?", "The board was divided over Microsoft's plans for the coming fiscal year."),
        ("What were net sales in 2026?", "47,112 net sales 2026 | 39,220 | 12 % | (3) % | 2026 2025 2024 | 10 | 11"),
        ("What were net sales in 2026?", "2026 net sales grew in every segment the Company reports on, it said."),
    ],
)
async def test_stub_writer_abstains_rather_than_quote_a_wrong_sentence(question, evidence):
    assert await _write(question, [evidence]) == ABSTAIN_TEXT


async def test_stub_writer_matches_spelled_out_titles_and_whole_words():
    cfo = "Kevan Parekh has served as Senior Vice President and Chief Financial Officer since January 2025."
    assert await _write("Who is Apple's CFO?", [EXHIBITS, cfo]) == f"{cfo} [2]"
    paid = "The Company declared a quarterly dividend of $0.91 per share payable in December 2026."
    assert await _write("What is Microsoft's dividend?", [CLOUD, paid]) == f"{paid} [2]"
    notes = "On November 10, 2026, Apple Inc. consummated the issuance and sale of its 4.000% Notes due 2029."
    assert await _write("What notes did Apple issue in November 2026?", [EXHIBITS, notes]) == f"{notes} [2]"


async def test_stub_writer_keeps_a_name_with_an_initial_whole_and_drops_the_list_marker():
    said = (
        "On November 4, 2026, Emma N. Walmsley, a member of the Board of Directors of Microsoft Corporation, "
        "informed the Company of her decision not to stand for re-election."
    )
    chunk = f"Item 5.02. Departure of Directors or Certain Officers. (b) {said} Ms. Walmsley will serve until then."
    assert await _write("Which Microsoft director is not standing for re-election?", [chunk]) == f"{said} [1]"


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


# ---------- the Bedrock adapter (docs/CONTRACTS.md "Bedrock models") ----------
class _Raises:
    def __init__(self, code: str):
        self.code = code

    def converse(self, **kwargs):
        raise ClientError({"Error": {"Code": self.code, "Message": "no"}}, "Converse")


def _reply(parts, stop="end_turn"):
    return {
        "stopReason": stop,
        "output": {"message": {"content": parts}},
        "usage": {"inputTokens": 3, "outputTokens": 5},
    }


def test_default_models_are_the_inference_profile_ids():
    fresh = Settings(_env_file=None)
    assert fresh.bedrock_generation_model == "us.anthropic.claude-sonnet-5-5"
    assert fresh.bedrock_verifier_model == "us.anthropic.claude-haiku-5-5"


@pytest.mark.parametrize(
    "role, cap, model",
    [
        (ModelRole.generate, 8000, "us.anthropic.claude-sonnet-5-5"),
        (ModelRole.verify, 4000, "us.anthropic.claude-haiku-5-5"),
        (ModelRole.understand, 2000, "us.anthropic.claude-haiku-5-5"),
    ],
)
async def test_bedrock_complete_sends_no_temperature_and_reads_text_parts_only(monkeypatch, role, cap, model):
    monkeypatch.setattr(
        "finance_agents.gateway.bedrock.settings.bedrock_generation_model", "us.anthropic.claude-sonnet-5-5"
    )
    monkeypatch.setattr(
        "finance_agents.gateway.bedrock.settings.bedrock_verifier_model", "us.anthropic.claude-haiku-5-5"
    )
    client = _Converse(
        _reply(
            [
                {"reasoningContent": {"reasoningText": {"text": "thinking it over", "signature": "s"}}},
                {"text": "Revenue grew. "},
                {"reasoningContent": {"redactedContent": b"x"}},
                {"text": "[1]"},
            ]
        )
    )
    text, usage = await BedrockGateway(client=client).complete(
        role, "sys", [{"role": "user", "content": "q"}], max_tokens=300
    )
    assert text == "Revenue grew. [1]" and usage.model == model and usage.output_tokens == 5
    assert client.kwargs["modelId"] == model  # the inference profile id, as configured
    assert client.kwargs["inferenceConfig"] == {"maxTokens": cap}  # no temperature, no top_p; room for thinking


@pytest.mark.parametrize("stop", ["refusal", "content_filtered", "guardrail_intervened"])
async def test_bedrock_refusal_raises_model_refused(stop):
    client = _Converse(_reply([{"text": "I can't help with that."}], stop))
    with pytest.raises(ModelRefused):
        await BedrockGateway(client=client).complete(ModelRole.generate, "sys", [{"role": "user", "content": "q"}])


async def test_bedrock_generate_failure_names_the_aws_error_code_and_the_model():
    gw = BedrockGateway(client=_Raises("AccessDeniedException"))
    with pytest.raises(ModelCallFailed) as err:
        await gw.complete(ModelRole.generate, "sys", [{"role": "user", "content": "q"}])
    assert err.value.code == "AccessDeniedException"
    assert "AccessDeniedException" in str(err.value) and gw.models[ModelRole.generate] in str(err.value)


async def test_bedrock_understand_and_plan_fall_back_out_loud():
    gw = BedrockGateway(client=_Raises("ThrottlingException"))
    take_notes()
    understood = await gw.understand("What was AAPL revenue in 2023?")
    assert understood.intent == QueryIntent.numeric  # the heuristic's answer
    assert take_notes() == ["classifier call failed (ThrottlingException); used the deterministic fallback"]
    calls = await gw.plan("What was AAPL revenue in 2023?", understood, ALL)
    assert [c.tool for c in calls] == ["facts_sql", "search_documents"]
    assert take_notes() == ["planner call failed (ThrottlingException); used the deterministic fallback"]
    assert take_notes() == []


async def test_bedrock_plan_reads_tool_use_after_reasoning_whatever_the_stop_reason():
    client = _Converse(
        _reply(
            [
                {"reasoningContent": {"reasoningText": {"text": "a figure: facts", "signature": "s"}}},
                {
                    "toolUse": {
                        "toolUseId": "1",
                        "name": "facts_sql",
                        "input": {"entity": "AAPL", "concept": "Revenues"},
                    }
                },
            ],
            stop="end_turn",
        )
    )
    calls = await _plan(BedrockGateway(client=client), "What was AAPL revenue in 2023?", ALL)
    assert [c.tool for c in calls] == ["facts_sql"]
    assert client.kwargs["inferenceConfig"] == {"maxTokens": 4000}
    assert client.kwargs["modelId"] == BedrockGateway().models[ModelRole.generate]
