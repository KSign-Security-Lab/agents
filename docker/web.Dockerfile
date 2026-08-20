# ===========================================================================
#  Next.js web tier. Multi-stage so the runtime image carries only the
#  standalone server output, not the toolchain.
# ===========================================================================
FROM node:22-bookworm-slim AS deps
WORKDIR /app
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund

FROM node:22-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY web/ ./
# pdf.js runs its parser in a web worker. Serving the worker from our own origin
# avoids depending on a CDN and keeps the viewer working on an isolated network.
RUN cp node_modules/pdfjs-dist/build/pdf.worker.min.mjs public/pdf.worker.min.mjs \
 && npm run build

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
RUN useradd -m -u 1001 nextjs
COPY --from=build --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nextjs /app/.next/static ./.next/static
COPY --from=build --chown=nextjs:nextjs /app/public ./public
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
