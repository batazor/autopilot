# Contributing

Thanks for your interest in improving Autopilot! Contributions of all kinds are
welcome — bug reports, scenarios, new game modules, docs, and fixes.

## Before you start

- Read the **[Contributor guide](CONTRIBUTOR.md)** for environment setup (uv,
  Docker, Tesseract, ADB, the Web UI) — that's the canonical dev-setup doc.
- Skim **[CLAUDE.md](CLAUDE.md)** for the architecture, module layout, and the
  scenario DSL conventions.
- By participating you agree to abide by our
  [Code of Conduct](CODE_OF_CONDUCT.md).

## Development workflow

```sh
uv sync --extra dev          # install deps + dev tools
docker compose up -d redis   # local Redis
uv run ruff check .          # lint
uv run pytest -q             # tests
```

- Use `uv run ...` for all Python commands (never bare `pip`/`python`).
- Add tests next to the module they protect (`games/<game>/<id>/tests/`).
- Keep changes focused; match the style and comment density of surrounding code.

## Submitting changes

1. Fork and create a topic branch.
2. Make your change with tests; ensure `ruff` and `pytest` pass.
3. Open a pull request describing **what** changed and **why**. Link any related
   issue.
4. The PR template will prompt you for a test/verification checklist.

## Reporting bugs & requesting features

Use the GitHub issue templates. For security issues, follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.

## License

By contributing, you agree that your contributions will be licensed under the
project's [MIT License](LICENSE).
