# Autopilot

Multi-account, game-agnostic Android bot: one worker per emulator instance, queue and state in Redis, screen text via local Tesseract OCR. Whiteout Survival is fully covered today; Kingshot and other games are on the roadmap.

> [!WARNING]
> **Disclaimer — read before use.** Autopilot automates third-party mobile games
> (Whiteout Survival, Kingshot, …). Automating gameplay almost certainly
> **violates those games' Terms of Service** and can get your account
> **suspended or permanently banned**. This project is provided for **educational
> and research purposes only**, with **no warranty** (see [LICENSE](LICENSE)).
> You are solely responsible for how you use it. The authors are not affiliated
> with, endorsed by, or connected to Century Games or any game publisher, and
> accept no liability for any consequences of use.

## Documentation

User-facing docs (installation, emulator config, troubleshooting, feature list, `docker-compose.prod.yml`) live in the public docs site, built from the [`landing/`](landing/) submodule:

**→ <https://batazor.github.io/autopilot-page/>**

For development setup (uv, Docker build, lint, tests) see [`CONTRIBUTOR.md`](CONTRIBUTOR.md).

## Quick reference (developers)

| Command | Role |
|:--------|:-----|
| `uv run play` | Worker + API + Next.js production build (local all-in-one) |
| `uv run bot` | Headless worker + scheduler |
| `uv run api` | FastAPI for Next.js Web UI |

Local dashboard: [`web/README.md`](web/README.md) — `uv run api` + `cd web && pnpm dev` → http://127.0.0.1:3000

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Contributor guide](CONTRIBUTOR.md). Please follow the
[Code of Conduct](CODE_OF_CONDUCT.md). Found a security issue? See
[SECURITY.md](SECURITY.md).

## Telemetry

Production Docker builds can ship anonymous operational metrics (and `ERROR`-level
logs) to a Grafana Cloud endpoint baked in at build time. What is sent, and how
to build without it, is documented in [`grafana/README.md`](grafana/README.md).
Builds without baked credentials (the default for forks and local builds) send
nothing.

## License

[MIT](LICENSE) © Login Viktor

## Links

- [Discord](https://discord.gg/62twnzKG9)
- [User docs site](https://batazor.github.io/autopilot-page/) (built from `landing/`)
- [Contributor guide](CONTRIBUTOR.md)
- [Web dashboard notes](web/README.md)
