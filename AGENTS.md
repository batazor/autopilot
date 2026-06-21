# Agent Instructions

- Use `uv` for all Python workflows in this repository.
- Run Python commands as `uv run ...` from the repo root, including `python`, `pytest`, `ruff`, scripts, and module entrypoints.
- Use `uv sync` / `uv add` for Python dependency management; do not use ad-hoc `pip` or manual virtualenv commands unless the user explicitly asks.
- Web UI commands under `web/` use **pnpm** (`pnpm install`, `pnpm dev`, `pnpm build`, `pnpm test`). The canonical lockfile is `web/pnpm-lock.yaml`; there is no `package-lock.json`.
- If you start any long-running process, server, watcher, supervisor, bot, or background helper for debugging or verification, stop it before handing control back unless the user explicitly asks to keep it running. Clean up only what you started; do not stop pre-existing user processes.
- For GitHub repository automation, use `gh api` with REST endpoints. This includes creating/updating refs, tags, releases, dispatches, and checking Actions status. Do not use GraphQL unless the user explicitly asks.
- Do not push GitHub repository automation with plain `git push` when the user asks for an API-based operation; create/update the needed refs via the GitHub REST API instead.
- Do not use `gh run watch` or polling loops for GitHub Actions status; make one-shot API requests instead.
