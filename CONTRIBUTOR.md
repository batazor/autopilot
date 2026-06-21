# Contributing

Setup for **editing the code**. End-users who just want to run the bot are better off with the [Docker quickstart in the README](README.md#%EF%B8%8F-installation--setup) — pulls the prebuilt images, no uv install needed.

## Prerequisites

| Requirement | Version | Download |
|:------------|:-------:|:---------|
| [uv](https://docs.astral.sh/uv/) | latest | [Install](https://docs.astral.sh/uv/getting-started/installation/) |
| Python | `3.13` (pinned by `.python-version`) | auto-installed by uv |
| Docker | compose v2 | [Get Docker](https://docs.docker.com/get-docker/) |
| Redis | 7+ | started via `docker compose` |
| Tesseract OCR | 5+ with `eng.traineddata` | `brew install tesseract` / OS package manager |
| Android Platform Tools (`adb`) | latest | [Download](https://developer.android.com/tools/releases/platform-tools) |
| BlueStacks | 5+ | [Download](https://www.bluestacks.com/) |
| Node.js (Web UI only) | 20+ | [nodejs.org](https://nodejs.org/) — `pnpm install` in `web/` |

> The emulator must be **720 × 1280, 320 DPI, English game language** — see the [Emulator Configuration](README.md#-emulator-configuration) section in the README for the full required-settings table.

### `adb` on PATH

Streamlit, the Next.js dev server, and Cursor often start with a reduced `PATH`. The UI defaults to `/opt/homebrew/bin/adb` (Homebrew on Apple Silicon); autodiscovery also checks `~/Library/Android/sdk/platform-tools/adb` and `/usr/local/bin/adb`.

Override either of:

- `ANDROID_HOME=/path/to/sdk`
- `worker.adb_executable: /full/path/to/adb` in `src/config/settings.yaml`

Verify:

```sh
adb devices
```

The serial column must match `bluestacks_window_title` loaded from `db/devices.yaml`.

## Setup

```sh
git clone https://github.com/batazor/autopilot.git
cd autopilot

# Python 3.13 + project deps (from uv.lock)
uv sync

# Just the supporting service — bot runs on the host via `uv run play`
docker compose up -d redis
```

Edit `src/config/settings.yaml` (`redis.url`, `ocr.tesseract_cmd`, worker settings) and `db/devices.yaml` (players per device) before the first run. `WOS_REDIS_URL`, `WOS_TESSERACT_CMD`, `TESSDATA_PREFIX`, and related env vars can override the YAML values.

## Databases (plain SQLite)

Every database (`db/state/state.db`, `src/modules/notify/data/notify_monitor.db`, the dreamscape `scenes.db`) is a **plain, unencrypted SQLite file** in WAL mode — there is nothing to configure. Open them with any SQLite tool or the stdlib `sqlite3` module.

**Upgrading a deployment that ran the old SQLCipher-encrypted build:** its `.db` files are still encrypted and the plain `sqlite3` driver can't open them. Convert them **once, with the bot/API/worker stopped**, before starting the new code:

```sh
uv run --with sqlcipher3-wheels python scripts/decrypt_databases.py   # decrypts in place, keeps *.encrypted.bak
```

It is idempotent (already-plaintext files are skipped) and writes a `<file>.encrypted.bak` next to each converted DB — delete those once you've confirmed the app reads the data. `state.db` and `notify_monitor.db` are not tracked in git, so this runs per machine/deployment. The diskcache template-search cache (`.cache/wos/search_positions/`) is regenerable and holds no secrets.

## Running

Entry points are defined in `pyproject.toml` under `[project.scripts]`:

| Command | Role |
|:--------|:-----|
| `uv run play` | API + Next.js production build (`next build` → `next start`; start worker from sidebar **Start bot**) |
| `uv run bot` | Headless worker + scheduler only |
| `uv run api` | FastAPI for the Next.js dashboard (Redis, previews, labeling API) |

### Web UI (recommended for local development)

Full operator dashboard: [`web/README.md`](web/README.md).

```sh
docker compose up -d redis
uv run play          # API + Next.js → http://127.0.0.1:3000/overview (Start bot in sidebar)
```

Requires **Node.js 20+** in `web/` (`pnpm` on PATH; `play` runs `pnpm install` once if needed). Keep BlueStacks running and the device visible in `adb devices` first.

**Split terminals** (optional): `uv run bot`, `uv run api`, `cd web && pnpm dev`.

**Labeling** (versions, basename promote): http://127.0.0.1:3000/labeling.

**Wiki FAQ sync** (live progress): http://127.0.0.1:3000/wiki → FAQ tab.

### Headless mode

```sh
uv run bot
# or
uv run python -m worker.supervisor
```

All UIs publish commands on `wos:ui:command:{instance_id}` and `wos:ui:command:scheduler`; every mode reads the same Redis state.

## Dev tools

```sh
uv sync --extra dev    # ruff + pytest
uv run ruff check .
uv run pytest -q
```

Module-owned tests should live next to the module they protect:

```text
modules/<id>/tests/test_*.py
modules/core/<id>/tests/test_*.py
```

Keep cross-cutting tests in root `tests/`. Pytest discovers both `tests/` and `modules/`, and shared fixtures live in the repo-level `conftest.py` so module-local tests can use them.

## Building Docker images locally

`docker-compose.yml` (no `.prod` suffix) builds `bot` and `ocr` from your local checkout instead of pulling from GHCR:

```sh
docker compose build
docker compose up -d
```

CI (`.github/workflows/docker.yml`) publishes `bot` and `web` images to GHCR on every push to `main` and on `v*.*.*` tags — used by [`docker-compose.prod.yml`](docker-compose.prod.yml) (`api` reuses the `bot` image).
