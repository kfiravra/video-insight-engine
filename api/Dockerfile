# ----------------------------
# Stage 1: Builder
# ----------------------------
FROM node:20-alpine AS builder

RUN corepack enable && corepack prepare pnpm@8.15.0 --activate

# bcrypt@5 falls back to a node-gyp source build when node-pre-gyp cannot fetch its
# prebuilt binary; the fallback needs a C++ toolchain. pnpm 8's bundled node-gyp 9
# imports distutils, gone from Python 3.12: py3-setuptools supplies it. Builder stage only.
RUN apk add --no-cache python3 py3-setuptools make g++

WORKDIR /app

COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY packages/shared ./packages/shared
COPY packages/types ./packages/types
COPY api ./api

# Scoped install (only API + workspace deps)
RUN pnpm install --frozen-lockfile --filter @vie/api...

# Build shared first, then types, then API
RUN pnpm --filter @vie/shared build && pnpm --filter @vie/types build && pnpm --filter @vie/api build

# Clean production bundle
RUN pnpm --filter @vie/api deploy --prod /prod/api

# ----------------------------
# Stage 2: Production
# ----------------------------
FROM node:20-alpine

WORKDIR /app

COPY --from=builder /prod/api/dist ./dist
COPY --from=builder /prod/api/node_modules ./node_modules
COPY --from=builder /prod/api/package.json ./

# Run as the unprivileged `node` user (uid 1000, ships with the base image).
# The app only reads /app — no chown needed.
USER node

EXPOSE 3000

CMD ["node", "dist/index.js"]
