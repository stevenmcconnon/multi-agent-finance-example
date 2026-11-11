# gateway/

The L3 Models layer: the only place the answer-writing models are spoken to.

## Files

| File | What it owns |
|---|---|
| `__init__.py` | `get_gateway()`, chosen by `HR_MODEL_BACKEND` (`fake` or `bedrock`); re-exports `FakeGateway`, `format_evidence`. |
| `bedrock.py` | `BedrockGateway`: Bedrock Converse `complete`, `understand`, and `plan` using native tool use; attaches the configured Bedrock Guardrail to every call. |
| `fake.py` | `FakeGateway`, the offline stub (it reports itself as `offline-stub`): deterministic; understands and plans with rules and builds answers only from the evidence in the prompt. |

## How it is used

`agent/graph.py` calls `get_gateway()` once, then `understand()`, `plan()` and `complete()` for the Planner, Writer and Judge roles. Evidence reaches both adapters as numbered `[n] text` lines.

## Rules that hold here

- Only this folder imports `boto3` for chat models. Embedding and rerank calls to Bedrock live in `retrieval/embed.py` and `retrieval/rerank.py` and are named in the allowlist of `test_boto3_only_behind_the_doors` (`tests/test_architecture.py`).
- The model proposes tool calls; it never runs them. The harness decides.
- Tool planning is tested offline for both adapters in `tests/test_gateway.py`.
- The Judge is a second model role, so a draft is checked by a separate call.
- `guardrailConfig` is present only when `BEDROCK_GUARDRAIL_ID` is configured; the adapter is tested with a stubbed Bedrock client.

## Production vs this demo

In production: Bedrock with a Claude model per role: Claude Sonnet 5.5 (`us.anthropic.claude-sonnet-5-5`) generates and plans, Claude Haiku 5.5 (`us.anthropic.claude-haiku-5-5`) verifies and classifies. Both are inference profile ids.
In this demo: the offline stub is the default, because tests and the local stack must run offline at no cost. `make up-bedrock` runs the local stack with the Claude models; that mode costs money per question and has not been run live yet (the adapter is tested with a stubbed client).

## Read next

- [`../agent/README.md`](../agent/README.md).
- [`../../docs/adr/0008-model-driven-tool-selection.md`](../../../docs/adr/0008-model-driven-tool-selection.md).
- [`../../../docs/GUARDRAILS.md`](../../../docs/GUARDRAILS.md).
