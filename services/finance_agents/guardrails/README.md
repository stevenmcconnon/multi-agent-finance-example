# guardrails/

Part of L5 Harness: deterministic checks on what enters and leaves a turn, the per-principal rate limit, and the Amazon Bedrock Guardrails door. The policy layer (Cedar, the tool registry, the retrieval ACL predicate) is the enforcement; this folder is the belt to its braces and the place personally identifiable information (PII) is scrubbed before a model, a log or an audit row sees it.

## Files

| File | What it owns |
|---|---|
| `input.py` | `check_input(question) -> InputVerdict`: length and control-character checks (hard failures), a prompt-injection pattern list (tags `injection_suspected`, never blocks), PII redaction to `[redacted-email]`, `[redacted-phone]`, `[redacted-ssn]`, `[redacted-card]`. `redact()` is reused by `authz/audit.py`. |
| `output.py` | `check_output(text, citations_count, reason) -> OutputVerdict`: PII redaction of the answer; `uncited` when an answer (reason `answered`) carries prose but no citation. An abstention or an action outcome is never flagged. |
| `bedrock.py` | `converse_guardrail_config()` (spliced into every Converse call by `gateway/bedrock.py`) and `BedrockGuardrail.apply(text, source)` over `bedrock-runtime.apply_guardrail`. Off when `BEDROCK_GUARDRAIL_ID` is empty; tested with a stubbed client, never run live. |
| `ratelimit.py` | `RateLimiter.retry_after(principal)`: Redis `INCR` + `EXPIRE` on `hr:rl:<org>:<user>:<minute>`, limit `HR_RATE_LIMIT_PER_MINUTE`; a Redis failure allows with a warning. |

## How it is used

`api/app.py` runs `rate_limited` as a dependency on `POST /chat` and `POST /chat/{id}/approve` (429 with `Retry-After`), `check_input` before the turn (422 `{"detail": {"reasons": [...]}}` on a hard failure, else the redacted question goes to the graph and a `guardrail` TurnEvent is the first server-sent event), and `check_output` on the final `answer` event. `authz/audit.py` passes every string through `redact`.

## Rules that hold here

- PII is redacted, never a reason to refuse; an injection match is tagged, never blocked, so the audit row keeps the evidence (`tests/test_guardrails.py::test_injection_is_tagged_not_blocked`, `test_pii_is_redacted_not_blocked`).
- Only hard failures block: empty after redaction, over length, control characters (`test_hard_failures_block`).
- The rate limit is shared across replicas through Redis and fails open (`test_rate_limit_allows_n_then_429_per_principal`, `test_rate_limit_allows_when_redis_is_down`).
- `guardrailConfig` reaches Converse only when a guardrail id is configured (`test_converse_receives_guardrail_config_only_when_configured`).

## Read next

- [`../../../docs/GUARDRAILS.md`](../../../docs/GUARDRAILS.md), every guardrail in the system in one table.
- [`../authz/README.md`](../authz/README.md), the enforcement these checks sit in front of.
