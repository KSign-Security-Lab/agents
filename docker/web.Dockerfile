# ===========================================================================
#  Next.js web tier, built as part of the pnpm workspace. Multi-stage so the
#  runtime image carries only the standalone server output, not the toolchain.
# ===========================================================================
FROM node:22-bookworm-slim AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY apps/web/package.json ./apps/web/
RUN pnpm install --frozen-lockfile --filter ./apps/web...

FROM node:22-bookworm-slim AS build
WORKDIR /app
RUN corepack enable
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/web/node_modules ./apps/web/node_modules
COPY package.json pnpm-workspace.yaml ./
COPY apps/web/ ./apps/web/
WORKDIR /app/apps/web
# pdf.js runs its parser in a web worker. Serving the worker from our own origin
# avoids depending on a CDN and keeps the viewer working on an isolated network.
# public/ isn't checked in (nothing else needs it), so it must be created here.
RUN mkdir -p public \
 && cp node_modules/pdfjs-dist/build/pdf.worker.min.mjs public/pdf.worker.min.mjs \
 && pnpm run build

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
RUN useradd -m -u 1001 nextjs
COPY --from=build --chown=nextjs:nextjs /app/apps/web/.next/standalone ./
COPY --from=build --chown=nextjs:nextjs /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=nextjs:nextjs /app/apps/web/public ./apps/web/public
USER nextjs
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
