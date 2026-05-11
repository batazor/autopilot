# Whiteout Survival autopilot

Multi-account bot: one worker per BlueStacks instance, queue and state in Redis, screen text via a separate OCR HTTP service.

**Discord:** [https://discord.gg/G8encVpD9](https://discord.gg/G8encVpD9)

## Screenshots

The Streamlit app (`uv run wos`) covers **gift codes**, **labeling and YAML scenarios**, and **runtime scenario debugging**. On GitHub, use the arrows to expand each screenshot.

<details>
<summary><strong>Gift codes</strong> — manage promotional codes from the DB hub (track and apply per account).</summary>

![Gift codes](docs/gift_code.png)

</details>

<details>
<summary><strong>Labeling and scenarios</strong> — capture references over ADB, mark OCR regions, and edit YAML scenarios (Wiki / scenario editor).</summary>

![Labeling and scenario tooling](docs/labeling.png)

</details>

<details>
<summary><strong>Scenario debugging</strong> — run scenarios against a live instance and inspect execution while the bot is running.</summary>

![Scenario runtime debug](docs/debug_mode.png)

</details>

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — installs Python **3.13** (see `.python-version`) and project deps from **`uv.lock`**.
- **macOS / Linux / Windows** — framebuffer capture uses **`adb exec-out screencap`** (same as taps).
- **BlueStacks** with the game; layout target **720×1280 @ 320 DPI** (see `layout/`).
- **ADB** (Android Platform Tools): taps and screenshots go through `adb`. **Streamlit/Cursor** often start with a reduced PATH — the UI defaults to **`/opt/homebrew/bin/adb`** (Homebrew on Apple Silicon); override with your own path or **`ANDROID_HOME`**. Autodiscovery also checks `~/Library/Android/sdk/platform-tools/adb` and `/usr/local/bin/adb`. Serial = **`bluestacks_window_title`** in `config/settings.yaml`. Set **`worker.adb_executable`** if the worker process cannot find `adb`.
- **Redis** — default URL `redis://localhost:6379/0` in `config/settings.yaml`.
- **OCR** — PaddleOCR HTTP service (default port **8000**): run via **`docker compose`** (see Run).

## Run

1. Start Redis and OCR (recommended):

   ```bash
   docker compose up -d redis ocr
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

**Dev tools:** `uv sync --extra dev`.

## Streamlit dashboard

The UI reads Redis state and publishes commands on **`wos:ui:command:{instance_id}`** and **`wos:ui:command:scheduler`**. For capture/labeling over **ADB**, run **`uv run wos`** on a machine with the repo and **adb**. **`docker compose`** in this repo starts **redis** and **ocr** only; the Streamlit app is not containerised here (see **`ui/Dockerfile`** if you build it elsewhere).
