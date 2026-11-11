"""Ragas over evals/datasets/qa.jsonl against the LIVE stack (Postgres corpus, configured gateway).

    uv run --extra evals python evals/ragas_eval.py [--suite ragas]

Metrics: faithfulness, answer relevancy, context precision (the reference-free variant: the dataset carries
no gold answers, ADR 0004). Skips, with the reason printed, when `ragas` is not installed, when
HR_MODEL_BACKEND is not `bedrock` (the judge needs a real model) or when DATABASE_URL does not answer.
Results go to `eval_records` (suite, case_id, thread_id, scores, passed).

Contexts are the chunks the turn CITED, read back by chunk id. Those ids came out of the ACL-filtered
retriever for this principal, so nothing a user may not read enters the eval dataset (README, "The security
invariant"). The judge model reads only that.

In production: the judge model is reached through gateway/ like every other model call.
In this demo: ragas requires a LangChain LLM, so ChatBedrockConverse is built here, eval-only and read-only,
because wrapping the gateway for ragas is more code than this sample needs.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))

from harness_rag import db
from harness_rag.config import settings

DATASET = Path(__file__).resolve().parent / "datasets" / "qa.jsonl"
THRESHOLDS = {"faithfulness": 0.8, "answer_relevancy": 0.6, "context_precision": 0.5}


def skip(reason: str, script: str = "ragas_eval") -> int:
    print(f"{script} skipped: {reason}")
    return 0


async def principal_for(user: str):
    from harness_rag.harness import devtoken, identity

    return await identity.verify_token(devtoken.mint(user))  # the production door: token -> DB-resolved Principal


async def cited_contexts(answer) -> list[str]:
    ids = [str(c.chunk_id) for c in answer.citations if c.chunk_id]
    if not ids:
        return [c.snippet or "" for c in answer.citations]
    rows = await db.fetch("SELECT content FROM chunks WHERE id = ANY(%s::uuid[])", (ids,))
    return [r["content"] for r in rows]


async def collect() -> list[dict]:
    from harness_rag.agent import graph

    await graph.startup()
    samples = []
    for case in (json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()):
        if case["expect"] != "answer":
            continue  # abstain cases are judged by evals/suites, not by a grader
        thread_id = f"ragas-{case['id']}"
        ans = await graph.run_turn(await principal_for(case["user"]), case["question"], thread_id)
        if ans.abstained:
            print(f"{case['id']}: abstained (not scored)")
            continue
        samples.append(
            {
                "case_id": case["id"],
                "thread_id": thread_id,
                "user_input": case["question"],
                "response": ans.text,
                "retrieved_contexts": await cited_contexts(ans),
            }
        )
    await graph.shutdown()
    return samples


async def record(suite: str, rows: list[dict]) -> None:
    for r in rows:
        await db.execute(
            "INSERT INTO eval_records (suite, case_id, thread_id, scores, passed) VALUES (%s, %s, %s, %s::jsonb, %s)",
            (suite, r["case_id"], r["thread_id"], json.dumps(r["scores"]), r["passed"]),
        )


async def main(suite: str = "ragas") -> int:
    try:
        from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
        from ragas import EvaluationDataset, SingleTurnSample, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import AnswerRelevancy, Faithfulness, LLMContextPrecisionWithoutReference
    except ImportError as exc:
        return skip(f"evals extra not installed ({exc}); run with `uv run --extra evals`")
    if settings.hr_model_backend != "bedrock":
        return skip("HR_MODEL_BACKEND is not bedrock; the judge needs a real model")
    if not await db.ping():
        return skip("DATABASE_URL does not answer")

    samples = await collect()
    if not samples:
        return skip("no non-abstained answers to score")
    judge = LangchainLLMWrapper(
        ChatBedrockConverse(model=settings.bedrock_verifier_model, region_name=settings.aws_region, temperature=0)
    )
    emb = LangchainEmbeddingsWrapper(
        BedrockEmbeddings(model_id=settings.bedrock_embedding_model, region_name=settings.aws_region)
    )
    dataset = EvaluationDataset(
        [
            SingleTurnSample(
                user_input=s["user_input"], response=s["response"], retrieved_contexts=s["retrieved_contexts"]
            )
            for s in samples
        ]
    )
    result = evaluate(
        dataset,
        metrics=[Faithfulness(), AnswerRelevancy(), LLMContextPrecisionWithoutReference()],
        llm=judge,
        embeddings=emb,
    )
    frame = result.to_pandas()
    rows = []
    for s, (_, scored) in zip(samples, frame.iterrows(), strict=True):
        scores = {
            "faithfulness": float(scored["faithfulness"]),
            "answer_relevancy": float(scored["answer_relevancy"]),
            "context_precision": float(scored["llm_context_precision_without_reference"]),
        }
        passed = all(scores[k] >= v for k, v in THRESHOLDS.items())
        rows.append({**s, "scores": scores, "passed": passed})
        print(f"{s['case_id']}: {'pass' if passed else 'FAIL'} {scores}")
    await record(suite, rows)
    await db.close_pool()
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[sys.argv.index("--suite") + 1] if "--suite" in sys.argv else "ragas")))
