# Whiteout Survival autopilot

Multi-account bot: one worker per BlueStacks instance, queue and state in Redis, screen text via local Tesseract OCR.

## Documentation

User-facing docs (installation, emulator config, troubleshooting, feature list, `docker-compose.prod.yml`) live in the public docs site, built from the [`page/`](page/) submodule:

**→ <https://batazor.github.io/autopilot-page/>**

For development setup (uv, Docker build, lint, tests) see [`CONTRIBUTOR.md`](CONTRIBUTOR.md).

## Quick reference (developers)

| Command | Role |
|:--------|:-----|
| `uv run play` | Worker + API + Next.js production build (local all-in-one) |
| `uv run bot` | Headless worker + scheduler |
| `uv run api` | FastAPI for Next.js Web UI |
| `uv run mcp` | MCP server (experimental) |

Local dashboard: [`web/README.md`](web/README.md) — `uv run api` + `cd web && npm run dev` → http://127.0.0.1:3000

## Links

- [Discord](https://discord.gg/62twnzKG9)
- [User docs site](https://batazor.github.io/autopilot-page/) (built from `page/`)
- [Contributor guide](CONTRIBUTOR.md)
- [Web dashboard notes](web/README.md)
