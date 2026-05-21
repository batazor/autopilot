import type { NextConfig } from "next";

const apiUrl = process.env.WOS_API_URL || "http://127.0.0.1:8765";

const nextConfig: NextConfig = {
  // Standalone output triggers the "Collecting build traces" pass; only the
  // prod Docker image actually consumes .next/standalone, so opt-in by env.
  output: process.env.WOS_BUILD_STANDALONE === "1" ? "standalone" : undefined,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiUrl}/api/:path*`,
      },
      {
        source: "/health",
        destination: `${apiUrl}/health`,
      },
    ];
  },
};

export default nextConfig;
