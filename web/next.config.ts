import type { NextConfig } from "next";

const apiUrl = process.env.WOS_API_URL || "http://127.0.0.1:8765";

// Cap static-generation workers when asked (e.g. `uv run play` building while a
// preview dev server is up): the default is one worker per core, which on an
// 18-core box spawns 17 workers and OOM-kills one under memory pressure. Only
// applied when WOS_BUILD_CPUS is set, so Docker/CI builds keep full parallelism.
const buildCpus = Number(process.env.WOS_BUILD_CPUS);
const cappedCpus =
  Number.isFinite(buildCpus) && buildCpus >= 1 ? Math.floor(buildCpus) : undefined;

const nextConfig: NextConfig = {
  // Instant Navigations (Next 16.3): dynamic-by-default + Stream/Cache/Block.
  // Every server-side `await` must Stream (<Suspense>), Cache ('use cache'),
  // or Block (`export const instant = false`); `useSearchParams` must sit in a
  // Suspense boundary. partialPrefetching prefetches one reusable shell per
  // route instead of one request per <Link> (kills the sidebar prefetch flurry).
  cacheComponents: true,
  partialPrefetching: true,
  // Limit build workers under memory pressure (set by the play launcher).
  ...(cappedCpus ? { experimental: { cpus: cappedCpus } } : {}),
  // Two Next servers sharing web/.next clobber each other (CSS chunk 404s),
  // so an auxiliary dev/preview server gets its own dist dir to coexist with
  // a running `uv run play` production server.
  distDir: process.env.NEXT_DIST_DIR || undefined,
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
