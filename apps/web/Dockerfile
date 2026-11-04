# ----------------------------
# Stage 1: Builder (pnpm workspace — mirrors api/Dockerfile)
# ----------------------------
FROM node:20-alpine AS builder

RUN corepack enable && corepack prepare pnpm@8.15.0 --activate

WORKDIR /app

# Build args for Vite (baked into the bundle at build time)
ARG VITE_API_URL=http://localhost:3000/api
ARG VITE_WS_URL=ws://localhost:3000
# Dev login is opt-in per environment — never default-on in an image
ARG VITE_ENABLE_DEV_LOGIN=false
# Sentry — empty DSN = SDK not initialized (see src/main.tsx)
ARG VITE_SENTRY_DSN=
ARG VITE_SENTRY_RELEASE=
# Contact address on the billing page's "tokens are added by the team" notes; empty = no link
ARG VITE_SUPPORT_EMAIL=

ENV VITE_API_URL=$VITE_API_URL \
    VITE_WS_URL=$VITE_WS_URL \
    VITE_ENABLE_DEV_LOGIN=$VITE_ENABLE_DEV_LOGIN \
    VITE_SENTRY_DSN=$VITE_SENTRY_DSN \
    VITE_SENTRY_RELEASE=$VITE_SENTRY_RELEASE \
    VITE_SUPPORT_EMAIL=$VITE_SUPPORT_EMAIL

COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY packages/shared ./packages/shared
COPY packages/types ./packages/types
COPY apps/web ./apps/web

# Scoped install (web + workspace deps only), from the committed lockfile
RUN pnpm install --frozen-lockfile --filter web...

# Build shared first, then types, then the web bundle
RUN pnpm --filter @vie/shared build \
    && pnpm --filter @vie/types build \
    && pnpm --filter web build

# ----------------------------
# Stage 2: Static serve (unprivileged nginx, runs as uid 101)
# ----------------------------
FROM nginxinc/nginx-unprivileged:1.29-alpine

COPY --from=builder /app/apps/web/dist /usr/share/nginx/html
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 5173

CMD ["nginx", "-g", "daemon off;"]
