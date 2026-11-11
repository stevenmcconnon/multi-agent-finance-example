// Zod mirrors of services/harness_rag/contracts.py and docs/CONTRACTS.md. Nobody redefines these.
import { z } from "zod";

export const Principal = z.object({
  user_id: z.string(),
  org_id: z.guid(),
  org_slug: z.string(),
  groups: z.array(z.string()).default([]),
  roles: z.array(z.string()).default([]),
  token_issued_at: z.string().nullable().optional(),
});
export type Principal = z.infer<typeof Principal>;

export const PolicyDecision = z.object({
  allow: z.boolean(),
  action: z.string(),
  resource: z.string(),
  reasons: z.array(z.string()).default([]),
  policy_ids: z.array(z.string()).default([]),
  fail_closed: z.boolean().default(false),
});
export type PolicyDecision = z.infer<typeof PolicyDecision>;

export const Citation = z.object({
  document_id: z.guid(),
  chunk_id: z.guid().nullable().optional(),
  title: z.string().nullable().optional(),
  uri: z.string(),
  section: z.string().nullable().optional(),
  version: z.string().nullable().optional(),
  snippet: z.string().nullable().optional(),
});
export type Citation = z.infer<typeof Citation>;

export const Verification = z.object({
  grounded: z.boolean(),
  confidence: z.number().min(0).max(1),
  unsupported_claims: z.array(z.string()).default([]),
  verifier_model: z.string().default(""),
});

export const Answer = z.object({
  text: z.string(),
  citations: z.array(Citation),
  confidence: z.number().min(0).max(1),
  abstained: z.boolean().default(false),
  verification: Verification.nullable().optional(),
  thread_id: z.string(),
  trace_id: z.string().nullable().optional(),
});
export type Answer = z.infer<typeof Answer>;

const RetrievalChunk = z.object({
  document_id: z.guid(),
  title: z.string().nullable().optional(),
  section: z.string().nullable().optional(),
  score: z.number().nullable().optional(),
});

export const TurnEvent = z.discriminatedUnion("type", [
  z.object({ type: z.literal("status"), data: z.object({ node: z.string(), message: z.string().default(""), agent: z.string().optional(), kind: z.enum(["deterministic", "model", "human"]).optional() }) }),
  z.object({ type: z.literal("policy"), data: PolicyDecision }),
  z.object({
    type: z.literal("retrieval"),
    data: z.object({ candidate_count: z.number().int(), chunks: z.array(RetrievalChunk) }),
  }),
  z.object({
    type: z.literal("tool"),
    data: z.object({
      tool: z.string(),
      ok: z.boolean(),
      latency_ms: z.number(),
      from_cache: z.boolean().default(false),
      error: z.string().nullable().optional(),
    }),
  }),
  z.object({ type: z.literal("token"), data: z.object({ text: z.string() }) }),
  z.object({
    type: z.literal("hitl"),
    data: z.object({ tool: z.string(), arguments: z.record(z.string(), z.unknown()), reason: z.string().default("") }),
  }),
  z.object({ type: z.literal("answer"), data: Answer }),
  z.object({ type: z.literal("error"), data: z.object({ code: z.string(), message: z.string() }) }),
]);
export type TurnEvent = z.infer<typeof TurnEvent>;

export const Checkpoint = z.object({
  checkpoint_id: z.string(),
  step: z.number().int(),
  node: z.string(),
  state_summary: z.unknown(),
  at: z.string(),
});
export type Checkpoint = z.infer<typeof Checkpoint>;

export const Document = z.object({
  id: z.guid(),
  title: z.string(),
  uri: z.string(),
  version: z.string().nullable().optional(),
  metadata: z.record(z.string(), z.unknown()).default({}),
});
export type Document = z.infer<typeof Document>;

export const TokenResponse = z.object({ token: z.string() });

export const CognitoTokens = z.object({ id_token: z.string() });
