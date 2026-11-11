# apps/web — Multi-Agent Finance UI

Vite + React 19 SPA (TypeScript strict, Tailwind v4, Zod, react-router). Talks only to the FastAPI service in `docs/CONTRACTS.md`; every payload is Zod-validated in `src/lib/schemas.ts`. No SSR, no server routes: `dist/` is static files.

## Run

```sh
cp .env.example .env
pnpm install
pnpm dev          # http://localhost:3000, API at VITE_API_URL
```

## Build

```sh
pnpm test && pnpm lint && pnpm build && pnpm preview   # test = node --test on the SSE framer; build = tsc -b && vite build -> dist/
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

Pages: `/` sign in · `/auth/callback` PKCE code exchange · `/chat` streamed turn timeline with HITL approve/deny · `/threads/:id` checkpoint replay · `/documents` what the signed-in identity may read.
