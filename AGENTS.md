# Agent Instructions

- Use `uv` for all Python workflows in this repository.
- Run Python commands as `uv run ...` from the repo root, including `python`, `pytest`, `ruff`, scripts, and module entrypoints.
- Use `uv sync` / `uv add` for Python dependency management; do not use ad-hoc `pip` or manual virtualenv commands unless the user explicitly asks.
- Web UI commands under `web/` still use the existing Node tooling (`npm run ...`).
