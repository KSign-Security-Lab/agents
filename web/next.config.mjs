/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
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
