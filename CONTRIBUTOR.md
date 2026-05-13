# Contributing

Setup for **editing the code**. End-users who just want to run the bot are better off with the [Docker quickstart in the README](README.md#%EF%B8%8F-installation--setup) — pulls the prebuilt images, no uv / paddleocr install needed.

## Prerequisites

| Requirement | Version | Download |
|:------------|:-------:|:---------|
| [uv](https://docs.astral.sh/uv/) | latest | [Install](https://docs.astral.sh/uv/getting-started/installation/) |
| Python | `3.13` (pinned by `.python-version`) | auto-installed by uv |
| Docker | compose v2 | [Get Docker](https://docs.docker.com/get-docker/) |
| Redis | 7+ | started via `docker compose` |
| Android Platform Tools (`adb`) | latest | [Download](https://developer.android.com/tools/releases/platform-tools) |
| BlueStacks | 5+ | [Download](https://www.bluestacks.com/) |

> The emulator must be **720 × 1280, 320 DPI, English game language** — see the [Emulator Configuration](README.md#-emulator-configuration) section in the README for the full required-settings table.

### `adb` on PATH

Streamlit (and Cursor) often start with a reduced `PATH`. The UI defaults to `/opt/homebrew/bin/adb` (Homebrew on Apple Silicon); autodiscovery also checks `~/Library/Android/sdk/platform-tools/adb` and `/usr/local/bin/adb`.

Override either of:

- `ANDROID_HOME=/path/to/sdk`
- `worker.adb_executable: /full/path/to/adb` in `config/settings.yaml`

Verify:

```sh
adb devices
```

The serial column must match `bluestacks_window_title` in `config/settings.yaml`.

## Setup

```sh
git clone https://github.com/batazor/whiteout-survival-autopilot.git
cd whiteout-survival-autopilot

# Python 3.13 + project deps (from uv.lock)
uv sync

# Just the supporting services — bot runs on the host via `uv run wos`
docker compose up -d redis ocr
```

Edit `config/settings.yaml` (`redis.url`, `ocr.url`, `instances`) and `db/devices.yaml` (players per device) before the first run.

## Running

```sh
# UI + worker + scheduler — all in one Streamlit process
uv run wos
```

Streamlit serves at <http://127.0.0.1:8501> (override with `WOS_STREAMLIT_PORT=8502`). Keep BlueStacks running and the device visible in `adb devices` first.

### Headless mode (separate worker + scheduler processes)

```sh
uv run wos-bot
# or
uv run python -m worker.supervisor
```

The UI publishes commands on `wos:ui:command:{instance_id}` and `wos:ui:command:scheduler`; both modes read the same Redis state.

## Dev tools

```sh
uv sync --extra dev    # ruff + pytest
uv run ruff check .
uv run pytest -q
```

## Building Docker images locally

`docker-compose.yml` (no `.prod` suffix) builds `bot` and `ocr` from your local checkout instead of pulling from GHCR:

```sh
docker compose build
docker compose up -d
```

CI (`.github/workflows/docker.yml`) publishes `bot` and `ocr` images to GHCR on every push to `main` and on `v*.*.*` tags — used by [`docker-compose.prod.yml`](docker-compose.prod.yml).
