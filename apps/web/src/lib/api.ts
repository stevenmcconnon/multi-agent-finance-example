// The only door to the API: Bearer token from localStorage, every payload parsed with Zod.
import { z } from "zod";
import { STREAM_CLOSED, errorSentence } from "./present";
import { Answer, Checkpoint, Config, Document, Principal, TokenResponse, TurnEvent } from "./schemas";
import { sseFramer } from "./sse";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
export const AUTH_MODE = import.meta.env.VITE_AUTH_MODE === "cognito" ? "cognito" : "local";
const TOKEN_KEY = "hr_token";

export const getToken = () => (typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY));
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
  }
}

function parse<T>(schema: z.ZodType<T>, raw: unknown, what: string): T {
  const r = schema.safeParse(raw);
  if (!r.success) throw new ApiError(`${what}: payload did not match contract (${z.prettifyError(r.error)})`);
  return r.data;
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(`Cannot reach the API at ${API_URL}. Is the stack running (\`make up\`)?`);
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    if (res.status === 401) {
      // dev tokens last 8 hours: drop the dead one, and the header sends the browser to sign-in
      clearToken();
      window.dispatchEvent(new Event("hr-expired"));
    }
    throw new ApiError(errorSentence(res.status, body), res.status);
  }
  return res;
}

async function getJson<T>(schema: z.ZodType<T>, path: string, what: string): Promise<T> {
  return parse(schema, await (await request(path)).json(), what);
}

export async function devToken(user: "alice" | "bob" | "carol"): Promise<string> {
  const res = await request("/auth/dev-token", { method: "POST", body: JSON.stringify({ user }) });
  return parse(TokenResponse, await res.json(), "dev-token").token;
}

// carol belongs to no reader group on purpose: separation of duties between reading filings and running write actions.
export const PERSONAS = [
  { user: "alice", role: "analyst", desc: "reads annual reports (10-K) and current reports (8-K)" },
  { user: "bob", role: "viewer", desc: "reads current reports (8-K) only" },
  { user: "carol", role: "operations admin", desc: "may run write actions after approval; reads no filings" },
] as const;

export async function switchPersona(user: "alice" | "bob" | "carol"): Promise<void> {
  setToken(await devToken(user));
  // the header refreshes /me from this; pages drop what they loaded as the previous persona
  window.dispatchEvent(new CustomEvent("hr-persona", { detail: user }));
}

let config: Promise<Config | null> | undefined;
/** Fetched once per page load. null when the API predates GET /config or cannot be reached: the UI then shows nothing. */
export const getConfig = () =>
  (config ??= fetch(`${API_URL}/config`)
    .then(async (res) => (res.ok ? (Config.safeParse(await res.json()).data ?? null) : null))
    .catch(() => null));

export const getMe = () => getJson(Principal, "/me", "GET /me");
export const listDocuments = (limit = 200) => getJson(z.array(Document), `/documents?limit=${limit}`, "GET /documents");
export const getReplay = (threadId: string) =>
  getJson(z.array(Checkpoint), `/threads/${encodeURIComponent(threadId)}/replay`, "GET replay");

// SSE parsed by hand over a ReadableStream because EventSource cannot send a POST body or an Authorization header.
async function streamEvents(res: Response, onEvent: (e: TurnEvent) => void): Promise<void> {
  if (!res.body) throw new ApiError(STREAM_CLOSED);
  let finished = false; // a turn ends with an answer, an approval request or an error event
  const framer = sseFramer(({ type, data }) => {
    let json: unknown;
    try {
      json = JSON.parse(data);
    } catch {
      throw new ApiError(`SSE event "${type}" carried non-JSON data`);
    }
    const event = parse(TurnEvent, { type, data: json }, `SSE event "${type}"`);
    if (event.type === "answer" || event.type === "hitl" || event.type === "error") finished = true;
    onEvent(event);
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    // a dropped connection rejects the read; an API restart just ends the stream early
    const { value, done } = await reader.read().catch(() => {
      throw new ApiError(STREAM_CLOSED);
    });
    if (done) break;
    framer.push(decoder.decode(value, { stream: true }));
  }
  framer.end();
  if (!finished) throw new ApiError(STREAM_CLOSED);
}

export async function streamChat(question: string, threadId: string, onEvent: (e: TurnEvent) => void): Promise<void> {
  const res = await request("/chat", {
    method: "POST",
    headers: { Accept: "text/event-stream" },
    body: JSON.stringify({ question, thread_id: threadId }),
  });
  await streamEvents(res, onEvent);
}

export async function approve(
  threadId: string,
  approved: boolean,
  onEvent: (e: TurnEvent) => void,
  note?: string,
): Promise<void> {
  const res = await request(`/chat/${encodeURIComponent(threadId)}/approve`, {
    method: "POST",
    headers: { Accept: "text/event-stream" },
    body: JSON.stringify({ approved, note }),
  });
  await streamEvents(res, onEvent);
}

export const errorMessage = (e: unknown) => (e instanceof Error ? e.message : String(e));
export type { Answer };
