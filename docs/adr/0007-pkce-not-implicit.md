# ADR 0007: Cognito authorization code with PKCE, never the implicit flow

Status: accepted · 2026-11

## Context

The web app is a public client (a React SPA served from CloudFront) that needs a user's identity token to
call the API. OAuth 2.0 offers the implicit flow (token in the redirect fragment) and the authorization
code flow; a public client cannot hold a client secret.

## Decision

Authorization code with PKCE: the SPA generates a code verifier, sends its S256 challenge to the Cognito
hosted UI, receives a one-time code at `/auth/callback`, and exchanges code plus verifier for tokens.
The Cognito app client has no secret and only the code grant enabled (`infra/terraform/modules/identity`).
Locally, `/auth/dev-token` mints HS256 tokens for the three seeded users and exists only when
`HR_JWT_MODE=local`.

## Alternatives

- **Implicit flow.** Tokens travel in the URL fragment: logged by browsers, leaked by referrers, no
  refresh, and deprecated by OAuth 2.1.
- **A backend-for-frontend with an HttpOnly session cookie.** Stronger token storage than `localStorage`;
  a second service to run. Named as the production upgrade in `docs/SECURITY.md`.
- **Client-credentials or a shared API key.** No user identity, which is the whole point.

## Consequences

- No secret in the SPA bundle; the code is single-use and bound to the verifier.
- The API verifies RS256 tokens against the pool's JWKS with issuer and audience (`harness/identity.py`)
  and still resolves groups from the database, so a token's `cognito:groups` is only a hint.
- The token sits in `localStorage` in this demo, a known limit; the BFF is the first production change.
