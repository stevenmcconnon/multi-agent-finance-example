# VITE_* are inlined at build time, so pass them as --build-arg.
FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.23.0 --activate
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
ARG VITE_API_URL=http://localhost:8000
ARG VITE_AUTH_MODE=local
ARG VITE_COGNITO_DOMAIN=
ARG VITE_COGNITO_CLIENT_ID=
ARG VITE_REDIRECT_URI=
ENV VITE_API_URL=$VITE_API_URL VITE_AUTH_MODE=$VITE_AUTH_MODE VITE_COGNITO_DOMAIN=$VITE_COGNITO_DOMAIN \
    VITE_COGNITO_CLIENT_ID=$VITE_COGNITO_CLIENT_ID VITE_REDIRECT_URI=$VITE_REDIRECT_URI
RUN pnpm build

# In production: the same dist/ is uploaded to S3 and served by CloudFront (infra/).
# In this demo: nginx serves it in docker-compose, because compose has no CDN.
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 3000
