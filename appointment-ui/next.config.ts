import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `postgres` is a server-only dependency; keep it out of the client bundle.
  serverExternalPackages: ["postgres"],
  // Self-contained server bundle for the Docker image (docker/Dockerfile.ui):
  // the runner stage copies .next/standalone instead of node_modules.
  output: "standalone",
};

export default nextConfig;
