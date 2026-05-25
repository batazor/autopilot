import type { NextConfig } from "next";

const apiUrl = process.env.WOS_API_URL || "http://127.0.0.1:8765";

const nextConfig: NextConfig = {
  // Standalone output triggers the "Collecting build traces" pass; only the
  // prod Docker image actually consumes .next/standalone, so opt-in by env.
  output: process.env.WOS_BUILD_STANDALONE === "1" ? "standalone" : undefined,
  // SVAR @svar-ui/react-* packages publish a broken `exports.require` path
  // (`./dist/index.cjs.js` vs. the actual `./dist/index.cjs`). The postinstall
  // script `scripts/fix-svar-exports.mjs` rewrites the paths; transpilation
  // through Next is a belt-and-braces layer so SSR also resolves cleanly.
  transpilePackages: [
    "@svar-ui/react-calendar",
    "@svar-ui/react-core",
    "@svar-ui/react-editor",
    "@svar-ui/react-menu",
    "@svar-ui/react-toolbar",
  ],
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
