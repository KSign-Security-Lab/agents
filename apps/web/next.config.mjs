import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // In the pnpm workspace, Next would otherwise infer the tracing root by
  // walking up to the nearest lockfile (the repo root) and silently nest the
  // standalone output under apps/web/ to match — being explicit means the
  // Docker build doesn't depend on Next's auto-detection guessing right.
  outputFileTracingRoot: path.join(__dirname, "../.."),
  reactStrictMode: true,
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: false },
  experimental: {
    // Uploads go through a route handler; the default 1MB body limit would
    // reject anything but the smallest documents.
    serverActions: { bodySizeLimit: "512mb" },
  },
};
export default nextConfig;
