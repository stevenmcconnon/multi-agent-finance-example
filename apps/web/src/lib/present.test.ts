import assert from "node:assert/strict";
import { test } from "node:test";
import { SESSION_EXPIRED, THREAD_OWNER, answerOutcome, errorSentence, modelName, sectionLabel } from "./present.ts";

test("model ids read as names, the stub never as 'fake'", () => {
  assert.equal(modelName("fake"), "offline stub");
  assert.equal(modelName("offline-stub"), "offline stub");
  assert.equal(modelName("us.anthropic.claude-sonnet-5-5"), "Claude Sonnet 5.5");
  assert.equal(modelName("us.anthropic.claude-haiku-5-5"), "Claude Haiku 5.5");
  assert.equal(modelName("amazon.titan-embed-text-v2:0"), "amazon.titan-embed-text-v2:0");
});

test("the preamble placeholder is not a section name", () => {
  assert.equal(sectionLabel("preamble"), "");
  assert.equal(sectionLabel(null), "");
  assert.equal(sectionLabel("Item 1A. Risk Factors"), "Item 1A. Risk Factors");
});

test("API errors become one sentence, never raw JSON", () => {
  assert.equal(errorSentence(403, '{"detail":"thread 123 belongs to another principal"}'), THREAD_OWNER);
  assert.equal(errorSentence(403, '{"detail":"nope"}'), "This identity is not permitted to do that.");
  assert.equal(errorSentence(401, '{"detail":"invalid token: Not enough segments"}'), SESSION_EXPIRED);
  assert.equal(errorSentence(429, '{"detail":"rate limit exceeded"}'), "Too many questions in a minute. Wait a moment and try again.");
  assert.equal(
    errorSentence(422, '{"detail":{"reasons":["too_long","something_new"]}}'),
    "The question was not accepted: it is too long; something new.",
  );
  assert.equal(
    errorSentence(422, '{"detail":[{"type":"string_too_short","msg":"String should have at least 1 character"}]}'),
    "The question was not accepted: String should have at least 1 character.",
  );
  assert.equal(errorSentence(500, "Internal Server Error"), "The API answered 500: Internal Server Error");
  assert.equal(errorSentence(502, ""), "The API answered 502.");
  for (const s of [errorSentence(422, '{"detail":{"x":1}}'), errorSentence(500, '{"detail":{"x":1}}')]) assert.ok(!s.includes("{"), s);
});

test("the headline follows Answer.reason and falls back to abstained when it is absent", () => {
  assert.equal(answerOutcome("answered", false).kind, "answer");
  assert.equal(answerOutcome(undefined, false).kind, "answer");
  assert.deepEqual(answerOutcome(undefined, true), { kind: "none", badge: "No answer", headline: "" });
  assert.equal(answerOutcome("no_relevant_evidence", true).headline, "None of the documents this person may read answer the question");
  assert.equal(answerOutcome("action_done", false).kind, "done");
  assert.equal(answerOutcome("action_forbidden", true).headline, "Not permitted for this role");
  assert.equal(answerOutcome("some_future_reason", true).badge, "No answer");
});

