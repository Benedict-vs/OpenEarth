# OpenEarth web — Vite build served by nginx, which also proxies /api → api:8000.
# Build context is the repo root. The build consumes the committed openapi.json /
# types.gen.ts (CI diff-checks they are in sync), so no API is needed at build time.

# ── builder ──────────────────────────────────────────────────────
FROM node:22-alpine AS builder
RUN corepack enable
WORKDIR /app

# Install deps first (cached unless the lockfile changes).
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY apps/web/ ./
RUN pnpm build

# ── runtime ──────────────────────────────────────────────────────
FROM nginx:alpine AS runtime
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
