import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `postgres` is a server-only dependency; keep it out of the client bundle.
  serverExternalPackages: ["postgres"],
};

export default nextConfig;
