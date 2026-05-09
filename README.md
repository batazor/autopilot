# Whiteout Survival autopilot

Multi-account bot: one worker per BlueStacks instance, queue and state in Redis, screen text via a separate OCR HTTP service.

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — installs Python **3.13** (see `.python-version`) and project deps from **`uv.lock`**.
- **macOS / Linux / Windows** — framebuffer capture uses **`adb exec-out screencap`** (same as taps).
- **BlueStacks** with the game; layout target **720×1280 @ 320 DPI** (see `layout/`).
- **ADB** (Android Platform Tools): taps and screenshots go through `adb`. **Streamlit/Cursor** often start with a reduced PATH — the UI defaults to **`/opt/homebrew/bin/adb`** (Homebrew on Apple Silicon); override with your own path or **`ANDROID_HOME`**. Autodiscovery also checks `~/Library/Android/sdk/platform-tools/adb` and `/usr/local/bin/adb`. Serial = **`bluestacks_window_title`** in `config/settings.yaml`. Set **`worker.adb_executable`** if the worker process cannot find `adb`.
- **Redis** — default URL `redis://localhost:6379/0` in `config/settings.yaml`.
- **OCR** — PaddleOCR HTTP service (default port **8000**): `docker-compose` image or local optional extra `ocr` in `pyproject.toml`.

## Run

1. Start Redis and OCR (recommended):

   ```bash
   docker compose up -d
   ```

2. Edit `config/settings.yaml` (`redis.url`, `ocr.url`, `instances`) and `db/devices.yaml` (players per device).

3. Install deps and run **one app** — UI and bot in a **single process** (bot as a background asyncio thread inside Streamlit):

   ```bash
   uv sync
   uv run wos
   ```

   Equivalent: `uv run streamlit run ui/app.py` from the repo root (editable install from `uv sync` wires imports).

   Streamlit: [http://127.0.0.1:8501](http://127.0.0.1:8501) (custom port: `WOS_STREAMLIT_PORT=8502 uv run wos`).

   **Bot only, no UI** (separate OS processes for workers + scheduler, legacy mode): `uv run wos-bot` or `uv run python -m worker.supervisor`.

Keep BlueStacks running and the device visible in `adb devices` before workers start.

**OCR without Docker:** `uv sync --extra ocr`, then:

```bash
uv run uvicorn ocr.service:app --host 127.0.0.1 --port 8000
```

**Dev tools:** `uv sync --extra dev`.

## Debug UI (Streamlit)

The app is **`ui/app.py`**; on load it starts the scheduler and workers (see above). To run a page only without the `wos` wrapper:

```bash
uv run streamlit run ui/app.py
```

The dashboard reads Redis and sends commands (`wos:ui:command:{instance_id}`, `wos:ui:command:scheduler`).

**Labeling:** one sidebar page — **`references/`** capture via **ADB** (serial = **`bluestacks_window_title`**) plus the OCR rectangle editor (**`streamlit-drawable-canvas`**) that writes **`area.json`** at the repo root. Same canvas as optional standalone `uv run streamlit run app.py`. Screenshots are not stored in Redis.

Or with Compose (set `redis.url` in `config/settings.yaml` to `redis://redis:6379/0` for the UI container):

```bash
docker compose up -d ui
```

Open [http://localhost:8501](http://localhost:8501).
