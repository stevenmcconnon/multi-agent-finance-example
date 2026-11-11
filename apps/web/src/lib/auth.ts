// Cognito authorization code + PKCE, hand-rolled on Web Crypto. Never the implicit flow (docs/CONTRACTS.md).
// In production: the same flow, with the hosted UI on a custom domain and token refresh.
// In this demo: the id token is kept in localStorage for its lifetime and sign-out only drops it locally, because the
// demo runs one browser against one API and a refresh-token dance adds nothing to what it proves.
import { CognitoTokens } from "./schemas";

const DOMAIN = import.meta.env.VITE_COGNITO_DOMAIN ?? "";
const CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID ?? "";
const VERIFIER_KEY = "hr_pkce_verifier";
const STATE_KEY = "hr_oauth_state";

const redirectUri = () => import.meta.env.VITE_REDIRECT_URI ?? `${window.location.origin}/auth/callback`;

const b64url = (bytes: ArrayBuffer | Uint8Array) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export async function startLogin(): Promise<void> {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  const q = new URLSearchParams({
    client_id: CLIENT_ID,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: redirectUri(),
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  window.location.assign(`${DOMAIN}/oauth2/authorize?${q}`);
}

/** Exchanges the callback's `code` at /oauth2/token and returns the id token. Throws a plain Error on any mismatch. */
export async function finishLogin(search: URLSearchParams): Promise<string> {
  const oauthError = search.get("error");
  if (oauthError) throw new Error(`${oauthError}: ${search.get("error_description") ?? ""}`);
  const code = search.get("code");
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  const stateOk = search.get("state") !== null && search.get("state") === sessionStorage.getItem(STATE_KEY);
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);
  if (!code || !verifier || !stateOk) throw new Error("Login callback is missing its code, verifier or state did not match");
  const res = await fetch(`${DOMAIN}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: CLIENT_ID,
      code,
      redirect_uri: redirectUri(),
      code_verifier: verifier,
    }),
  });
  if (!res.ok) throw new Error(`Token exchange failed: ${res.status} ${await res.text().catch(() => "")}`);
  const parsed = CognitoTokens.safeParse(await res.json());
  if (!parsed.success) throw new Error("Token exchange response did not match contract (no id_token)");
  return parsed.data.id_token;
}
