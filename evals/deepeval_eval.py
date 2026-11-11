"""DeepEval over evals/datasets/qa.jsonl against the LIVE stack.

    uv run --extra evals python evals/deepeval_eval.py

Metrics: faithfulness, answer relevancy, contextual relevancy. DeepEval's contextual PRECISION needs a gold
answer per case and the dataset carries none (ADR 0004), so contextual relevancy stands in for it.
The judge is the repo's own model gateway (ModelRole.verify), wrapped as a DeepEvalBaseLLM: no second vendor
door. Skips with the reason when `deepeval` is missing, HR_MODEL_BACKEND is not `bedrock`, or the database
does not answer. Results go to `eval_records`; the same cited-chunks rule as ragas_eval.py keeps the eval
dataset inside the caller's permissions.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from finance_agents import db
from finance_agents.config import settings
from finance_agents.contracts import ModelRole

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ragas_eval import collect, record
from ragas_eval import skip as _skip


def skip(reason: str) -> int:
    return _skip(reason, "deepeval_eval")


THRESHOLD = 0.7


async def main(suite: str = "deepeval") -> int:
    try:
        from deepeval.metrics import AnswerRelevancyMetric, ContextualRelevancyMetric, FaithfulnessMetric
        from deepeval.models import DeepEvalBaseLLM
        from deepeval.test_case import LLMTestCase
    except ImportError as exc:
        return skip(f"evals extra not installed ({exc}); run with `uv run --extra evals`")
    if settings.hr_model_backend != "bedrock":
        return skip("HR_MODEL_BACKEND is not bedrock; the judge needs a real model")
    if not await db.ping():
        return skip("DATABASE_URL does not answer")

    from finance_agents.gateway import get_gateway

    gateway = get_gateway()

    class GatewayJudge(DeepEvalBaseLLM):
        def load_model(self):
            return gateway

        def get_model_name(self) -> str:
            return settings.bedrock_verifier_model

        async def a_generate(self, prompt: str, schema=None) -> str:
            text, _ = await gateway.complete(
                ModelRole.verify, "You are an evaluation judge.", [{"role": "user", "content": prompt}], max_tokens=1024
            )
            return text

        def generate(self, prompt: str, schema=None) -> str:
            return asyncio.run(self.a_generate(prompt, schema))

    samples = await collect()
    if not samples:
        return skip("no non-abstained answers to score")
    judge = GatewayJudge()
    metrics = [
        FaithfulnessMetric(threshold=THRESHOLD, model=judge),
        AnswerRelevancyMetric(threshold=THRESHOLD, model=judge),
        ContextualRelevancyMetric(threshold=THRESHOLD, model=judge),
    ]
    rows = []
    for s in samples:
        case = LLMTestCase(
            input=s["user_input"], actual_output=s["response"], retrieval_context=s["retrieved_contexts"]
        )
        scores = {}
        for m in metrics:
            await m.a_measure(case)
            scores[type(m).__name__.removesuffix("Metric").lower()] = float(m.score or 0.0)
        passed = all(v >= THRESHOLD for v in scores.values())
        rows.append({**s, "scores": scores, "passed": passed})
        print(f"{s['case_id']}: {'pass' if passed else 'FAIL'} {scores}")
    await record(suite, rows)
    await db.close_pool()
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
