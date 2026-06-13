# Agent Instructions

- Use `uv` for all Python workflows in this repository.
- Run Python commands as `uv run ...` from the repo root, including `python`, `pytest`, `ruff`, scripts, and module entrypoints.
- Use `uv sync` / `uv add` for Python dependency management; do not use ad-hoc `pip` or manual virtualenv commands unless the user explicitly asks.
- Web UI commands under `web/` still use the existing Node tooling (`npm run ...`).
- If you start any long-running process, server, watcher, supervisor, bot, or background helper for debugging or verification, stop it before handing control back unless the user explicitly asks to keep it running. Clean up only what you started; do not stop pre-existing user processes.
- For GitHub operations that need the API, use `gh api` with REST endpoints. Do not use GraphQL for repository automation unless the user explicitly asks.
- Do not use `gh run watch` or polling loops for GitHub Actions status; make one-shot API requests instead.
