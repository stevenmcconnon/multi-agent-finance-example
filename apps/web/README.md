# apps/web — Multi-Agent RAG Finance Assistant UI

Vite + React 19 SPA (TypeScript strict, Tailwind v4, Zod, react-router). Talks only to the FastAPI service in `docs/CONTRACTS.md`; every payload is Zod-validated in `src/lib/schemas.ts`. No SSR, no server routes: `dist/` is static files.

## Run

```sh
cp .env.example .env
pnpm install
pnpm dev          # http://localhost:3000, API at VITE_API_URL
```

## Build

```sh
pnpm test && pnpm lint && pnpm build && pnpm preview   # test = node --test on the SSE framer, the phase grouping and the wording helpers; build = tsc -b && vite build -> dist/
docker build -t multi-agent-finance-example-web --build-arg VITE_API_URL=http://api:8000 .   # nginx:alpine serving dist/ on :3000 with SPA fallback
```

## Deploy (S3 + CloudFront)

`VITE_*` values are inlined at build time, so build once per environment, then ship the folder:

```sh
VITE_API_URL=https://api.example.com VITE_AUTH_MODE=cognito VITE_COGNITO_DOMAIN=... VITE_COGNITO_CLIENT_ID=... pnpm build
aws s3 sync dist/ s3://<bucket>/ --delete
aws cloudfront create-invalidation --distribution-id <id> --paths "/index.html"
```

`dist/assets/*` is content-hashed and can be cached forever; only `index.html` needs invalidating. The CloudFront distribution must map 403/404 to `/index.html` (200) so client routes like `/threads/<id>` and `/auth/callback` load on a hard refresh; `nginx.conf` does the same for the container (`try_files $uri /index.html`).

## Env

| var | meaning |
|---|---|
| `VITE_API_URL` | API base, default `http://localhost:8000` |
| `VITE_AUTH_MODE` | `local` (alice/bob/carol via `POST /auth/dev-token`) or `cognito` (hosted UI, authorization code + PKCE, callback at `/auth/callback`; never the implicit flow) |
| `VITE_COGNITO_DOMAIN`, `VITE_COGNITO_CLIENT_ID`, `VITE_REDIRECT_URI` | cognito mode only; redirect URI defaults to `<origin>/auth/callback` |

Pages: `/` sign in · `/auth/callback` PKCE code exchange · `/chat` streamed, color-coded turn phases with
guardrail status and human-in-the-loop (HITL) approve/deny · `/threads/:id` checkpoint replay · `/documents`
what the signed-in identity may read. `src/lib/schemas.ts` mirrors every `TurnEvent`, including the input and
output `guardrail` events; `src/lib/timeline.ts` maps those events to the reader-facing phase cards.
`src/lib/present.ts` owns the wording: model ids as names (the local stub reads "offline stub"), API errors as one
sentence (never raw JSON), and the answer headline chosen from `Answer.reason`. The header shows which model backend
is running from `GET /config`; both that endpoint and `Answer.reason` are optional, so the app still works against
an API that predates them.

Session rules: a 401 clears the token and returns to sign-in with a notice; switching persona in the header starts a
new thread (a thread belongs to the person who opened it) and reloads the Documents and Replay pages.
