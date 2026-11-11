# apps/

The L7 Experience layer: user-facing clients. There is one, the web app.

## Files

| Path | What it owns |
|---|---|
| `web/` | React + Vite single-page app: sign-in, chat, documents, replay, approvals. See [`web/README.md`](web/README.md). |

## How it is used

`make web` runs the dev server (`pnpm dev`); `make up` serves a build through nginx on port 3000. It calls the HTTP routes in `services/finance_agents/api/app.py`.

## Rules that hold here

- The client holds no secrets and never talks to the database; every call goes through the API with a bearer token.
- Sign-in on Amazon Web Services (AWS) is Cognito authorization code with PKCE ([ADR 0007](../docs/adr/0007-pkce-not-implicit.md)).

## Production vs this demo

In production: one person, one Cognito sign-in, and the build served from S3 behind CloudFront.
In this demo: a persona switcher (alice, bob, carol) in local mode and an nginx image, because the point is to show different permissions side by side.

## Read next

- [`web/README.md`](web/README.md).
- [`../docs/DEMO.md`](../docs/DEMO.md), the five-step walkthrough.
