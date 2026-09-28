FROM node:22-alpine AS deps
WORKDIR /web
COPY package.json package-lock.json* ./
RUN npm install

FROM node:22-alpine AS builder
WORKDIR /web
COPY --from=deps /web/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001
# `output: standalone` emits a self-contained server, so the runtime image
# carries no node_modules tree of its own.
COPY --from=builder --chown=nextjs:nodejs /web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /web/.next/static ./.next/static
# `standalone` does not include public/; without this the self-hosted font 404s
# and the Blush theme silently falls back to a system face.
COPY --from=builder --chown=nextjs:nodejs /web/public ./public
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
