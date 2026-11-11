// The only door to the API: Bearer token from localStorage, every payload parsed with Zod.
import { z } from "zod";
import { Answer, Checkpoint, Document, Principal, TokenResponse, TurnEvent } from "./schemas";
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
  const res = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(`${res.status} ${res.statusText}${body ? `: ${body.slice(0, 300)}` : ""}`, res.status);
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

export const PERSONAS = [
  { user: "alice", role: "analyst" },
  { user: "bob", role: "viewer" },
  { user: "carol", role: "admin" },
] as const;

export async function switchPersona(user: "alice" | "bob" | "carol"): Promise<void> {
  setToken(await devToken(user));
  window.dispatchEvent(new Event("hr-persona")); // the header refreshes /me from this
}

export const getMe = () => getJson(Principal, "/me", "GET /me");
export const listDocuments = (limit = 200) => getJson(z.array(Document), `/documents?limit=${limit}`, "GET /documents");
export const getReplay = (threadId: string) =>
  getJson(z.array(Checkpoint), `/threads/${encodeURIComponent(threadId)}/replay`, "GET replay");

// SSE parsed by hand over a ReadableStream because EventSource cannot send a POST body or an Authorization header.
async function streamEvents(res: Response, onEvent: (e: TurnEvent) => void): Promise<void> {
  if (!res.body) throw new ApiError("empty stream");
  const framer = sseFramer(({ type, data }) => {
    let json: unknown;
    try {
      json = JSON.parse(data);
    } catch {
      throw new ApiError(`SSE event "${type}" carried non-JSON data`);
    }
    onEvent(parse(TurnEvent, { type, data: json }, `SSE event "${type}"`));
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    framer.push(decoder.decode(value, { stream: true }));
  }
  framer.end();
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
