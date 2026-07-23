# Production image for the Next.js UI (admin console + patient portal).
# NEXT_PUBLIC_* values are baked in at BUILD time — pass the public API URL
# the patient's browser will use:
#   docker compose build ui   (reads NEXT_PUBLIC_API_URL from .env)

FROM node:22-alpine AS builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

# `output: "standalone"` bundles the server + needed node_modules only.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
