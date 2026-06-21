# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**Autopilot** is a multi-account, game-agnostic Android bot built on a **scenario-driven DSL** (YAML) and **overlay engine** (template + OCR matching). Whiteout Survival is fully covered today; Kingshot and other games are on the roadmap (engine has no game-specific code — only the scenario set under `games/<game>/` is per-game). The codebase uses **uv** for Python dependencies, **Redis** for multi-instance state/queue, and a **Next.js** dashboard (`web/`) backed by **FastAPI** (`src/api/`). Production Docker (`docker-compose.prod.yml`) runs **Next.js on :3000**, **API on :8765**, and a headless **bot** worker. Local **`uv run play`** starts API + Next.js only — the worker is **not** started automatically; press **Start bot** in the dashboard sidebar to spawn it (or run `uv run bot` in a separate terminal for headless mode).

## Key Commands

### Agent Tooling Rules

- Use `uv run ...` for all Python commands from the repo root, including `python`, `pytest`, `ruff`, scripts, and module entrypoints.
- For GitHub operations that need the API, use `gh api` with REST endpoints. Do not use GraphQL for repository automation unless explicitly requested.
- Do not use `gh run watch` or polling loops for GitHub Actions status; make one-shot API requests instead.
- **Always clean up after yourself.** Any process or server you start during a task — `uv run api`, `uv run bot`/`worker.supervisor`, preview dev servers (Next on `:3100`, Astro/landing on `:4321`), `docker compose` services, etc. — must be stopped before you finish, unless the user asked you to leave it running. Leftovers hog ports/memory and silently break later checks (a dead `uv run api` shows "API offline" in the dashboard). Before wrapping up, `ps`/`lsof` for anything you spawned and stop it; never kill processes the user started themselves.

### Development Setup
```sh
uv sync                           # Install deps + Python 3.13
docker compose up -d redis        # Start Redis (local dev only)
```

### Running

**Web UI (recommended for local dev)** — see [`web/README.md`](web/README.md):

```sh
docker compose up -d redis
uv run play          # API + Next.js → http://127.0.0.1:3000/overview
                     # then press "Start bot" in the dashboard sidebar to spawn the worker
```

**Split terminals** (optional): `uv run bot`, `uv run api`, `cd web && npm run dev`.

**Headless mode** (worker + scheduler only, no UI process):

```sh
uv run bot
# or
uv run python -m worker.supervisor
```

### Inspecting & controlling the bot (`botctl`)

`botctl` is the agent-facing layer for seeing what the bot is doing and driving
it — one text-first command that consolidates the ~30 Redis keys, the SQLite
state, and the control entry points the dashboard/API use. It is **headless**:
reads hit Redis/SQLite directly and control imports the service functions, so it
works whether or not `uv run api` is up. Every command takes `--json`. Source
lives in `src/agentctl/` (`core.py` is the single source of truth; the CLI and
the MCP server are thin presenters). **Start here when you need bot state** —
don't hand-roll Redis lookups.

```sh
uv run botctl status               # fleet snapshot: per-instance state/screen/player/task/queue
uv run botctl state bs1            # full per-instance detail
uv run botctl queue bs1 --history  # pending + running (+ recent history)
uv run botctl history bs1 -n 10    # recent executions (ok/fail, reason, duration)
uv run botctl trace bs1            # last scenario's step-by-step trace
uv run botctl screenshot bs1       # path to the live preview PNG → then Read that path to SEE the screen
uv run botctl player 401227964 buildings.levels   # per-account SQLite state (dot-key filter)
uv run botctl scenarios --grep mail               # list DSL scenarios + metadata
uv run botctl devices              # devices + backends + adb-online

# control (enqueue work / send commands — device taps still go through click-approval)
uv run botctl run check_main_city --inst bs1 --player 401227964   # enqueue now
uv run botctl pause bs1            # / resume / abort [--restart]
uv run botctl bot start|stop|status                              # local worker lifecycle
uv run botctl queue-remove <task_id> | queue-run-now <task_id> | queue-clear bs1
```

When an instance is unambiguous (one configured) the id is optional. Reads are
side-effect free; control only enqueues tasks or sends pause/resume/abort — it
never taps the device, so click-approval mode keeps gating real taps.

**As MCP tools (optional):** the same surface is exposed as native tools
(`bot_status`, `bot_run`, `bot_screenshot`, …) via a stdio MCP server. It's
registered in `.mcp.json` (`autopilot-bot`); the server needs the `agent` extra
(`uv sync --extra agent`, pulled in automatically by the `.mcp.json` launch).

### Testing & Linting

```sh
uv sync --extra dev               # Add dev tools (ruff + pytest)
uv run ruff check .               # Lint check
uv run pytest -q                  # Run all tests
uv run pytest games/wos/heroes/heroes/planner/tests/  # Run single module's tests
```

Tests live **next to the module** they protect:
- `games/<game>/<id>/tests/test_*.py`
- `games/<game>/core/<id>/tests/test_*.py`
- Cross-cutting tests go in root `tests/`

Fixtures are shared in repo-level `conftest.py`.

### Docker (Production Images)

```sh
# Build locally from checkout
docker compose build
docker compose up -d

# Or use pre-built images (production)
docker compose -f docker-compose.prod.yml up -d --pull always
```

## Architecture

### High-Level Flow

1. **Worker** (`src/worker/`) — one process per ADB device (emulator instance); runs scenario tasks pulled from Redis queue
2. **Scheduler** (`src/scheduler/`) — interval-driven scenarios (cron) publish to the queue immediately on boot, throttled thereafter
3. **Overlay Engine** (`src/analysis/`) — template + OCR matching; runs on every bot tick to detect UI state and populate Redis
4. **API** (`src/api/`) — FastAPI for Redis state, previews, labeling save, wiki, queue commands (used by Next.js)
5. **Web UI** (`web/`) — Next.js dashboard (primary local operator UI; proxies `/api` to FastAPI)
6. **Dashboard helpers** (`src/dashboard/`) — Redis state / labeling / area.json / preview helpers backing the FastAPI server (no UI framework)
7. **Modules** (`games/<game>/`) — feature domains (e.g., heroes, mail, building); each exports `analyze/analyze.yaml` rules and DSL scenarios

### Directory Structure

```
src/
  adb/                # ADB wrapper + screenshot capture
  analysis/           # Overlay engine: template match, OCR, red-dot, color checks
  config/             # Settings loading (YAML + env var overrides)
  layout/             # Region/screen definitions + red-dot detector
  navigation/         # Screen graph (node routing)
  ocr/                # Tesseract OCR wrapper
  scenarios/          # Framework-level scenarios (reconnect, welcome-back, etc.)
  scheduler/          # Cron + interval-driven task publishing
  services/           # App lifecycle (init, close, instance sessions)
  tasks/              # DSL step executor (match, click, cond, while_match, etc.)
  api/                # FastAPI (Redis, labeling, wiki, queue — backs Next.js)
  dashboard/          # Dashboard-backing helpers (Redis state, labeling, area.json, previews)
  worker/             # Worker process entry; supervisor (multiprocess restart, backoff)
  modules/            # Game-agnostic services (not per-game DSL modules)
    notify/           # Notification monitor (dumpsys → Redis queue); import `modules.notify`

web/                  # Next.js operator dashboard (see web/README.md)

games/                # Per-game module tree (Phase 3: replaces top-level modules/)
  wos/                # Whiteout Survival
    core/             # Core features (building, heroes, shop, main_city, etc.)
    backpack/         # Resource/speedup/gear scheduler
    mail/             # Mail claim + gift handling
    gift_codes/       # Gift code hub + redemption
    alliance/         # Alliance operations
    deals/            # Periodic deals / limited-time offers
    events/           # Event-specific automations (trials, 7-day, etc.)
    vip/              # VIP daily login check
    db/buildings/     # Static reference data (building specs, etc.)
  kingshot/           # Kingshot (in progress)
    core/             # Core features (main_city)
    alliance/         # Alliance operations (help)
    events/           # Event-specific automations (fishing_tournament)

temporal/             # Live ADB rolling/approval previews (gitignored, regenerated per tick)
db/state/state.db     # SQLite: devices + accounts + per-player state (canonical, multi-game)
```

### Key Concepts

#### Modules

Each module is a self-contained feature domain:

```
games/wos/heroes/
  module.yaml         # Module manifest (id, title, scenarios/analyze/area/references paths)
  area.yaml           # Screen/region definitions (flows into area.json merge)
  analyze/
    analyze.yaml      # Overlay engine rules (detect hero UI elements)
  scenarios/
    *.yaml            # DSL scenarios (e.g., promote_hero.yaml, claim_recruit.yaml)
  references/
    crop/*.png        # Template crops referenced by area.yaml regions
  exec.py             # [optional] Python DSL `exec:` handlers (auto-discovered)
  tests/
    test_*.py         # Unit tests for module logic
```

A module is declared by **`module.yaml`** (parsed by `src/config/module_registry.py` and
`src/config/module_exec_registry.py`) — there is **no `__init__.py` export contract**. Fields:
- **`id`** — unique identifier (defaults to the directory name)
- **`title`** / **`description`** — human-facing labels
- **`enabled`** — whether the module loads (`true`/`false`)
- **`scenarios`** — directory of DSL scenario YAMLs (default `scenarios`)
- **`analyze`** — overlay-rule file (default `analyze/analyze.yaml`)
- **`area`** — region definitions (default `area.yaml`/`area.yml`/`area.json`)
- **`references`** — template-crop directory (default `references`)
- **`exec`** — [optional] path to a Python file with `exec:` step handlers (default: `exec.py` if present)
- **`wiki`** / **`default_ref`** — [optional] wiki and default-reference settings

Per-user settings live as step args inside scenarios, not a Python `MODULE_CONFIG` dict.

#### Overlay Engine & Analyzers

The overlay engine runs every tick and detects **UI state** using:
- **Template matching** (`findIcon`) — crop-based image search with confidence threshold
- **OCR** (`text`) — screen text detection via Tesseract
- **Color checks** (`color_check`) — dominant color in a region
- **Red-dot detection** (`isRedDot`) — notification badge presence (programmatic, no template needed)

Analyzer rules live in `games/<game>/*/analyze/analyze.yaml`. Example:

```yaml
screens: [main_city]           # Only run this rule when on main_city screen
regions:
  - name: workers
    action: findIcon
    template: workers_icon.png
    threshold: 0.85
    has_red_dot: true          # Enable red-dot badge detection
```

**Rules to remember** (see `.cursor/rules/wos-overlay-actions.mdc`):
- Use **`findIcon`** in analyzer YAML (not `exist`)
- Use **`exist`** in `area.json` (labeled via UI)
- Both map to the same overlay action internally
- Red-dot detection requires `has_red_dot: true` in `area.json` and `isRedDot:` in DSL steps

#### DSL Scenarios (YAML)

Scenarios are **declarative task workflows**. Common steps:

```yaml
device_level: true             # Device-level (not account-level) scenario
cron: "0 */2 * * *"           # Run every 2 hours (optional)
max_reschedule_delay: 1h       # Max retry delay if scenario fails

steps:
  - match: workers             # Wait for overlay rule "workers" to be true
    isRedDot: true             # Optional: only proceed if red-dot is lit
    steps:
      - click: workers         # Tap the region
      - wait: 1s               # Pause

  - cond: currentNode != main_city  # Skip if we're not on main_city
    steps:
      - push_scenario: some_other_scenario

  - while_match: popup_close
    max: 1                     # Dismiss at most 1 popup
    strict: false              # Non-strict: zero matches → success
    steps:
      - click: popup_close
      - wait: 500ms
```

**Key DSL fields:**
- **`match: <region>`** — wait for region to be visible (fails scenario if missing)
- **`while_match: <region>`** — loop while region exists (default: non-strict on device-level)
- **`isRedDot: true|false`** — filter by red-dot presence (must be `has_red_dot: true` in `area.json`)
- **`cond: <guard>`** — skip step if condition fails (e.g., `currentNode != main_city`, `<field> ~= "text"`)
- **`click: <region>`** — tap region center
- **`push_scenario: <name>`** — switch to another scenario
- **`wait: <duration>`** — pause (e.g., `500ms`, `2s`)

See `.cursor/rules/wos-overlay-actions.mdc` for red-dot filters, `cond` syntax, and optional-tap patterns.

#### Labeling Editor

`area.yaml` is editable directly — hand-edit it (and the reference crops under
`references/crop/`) when that's the quickest path; just keep each region's
`bbox` consistent with its crop. The Labeling UI is a convenience for
capturing/cropping, not a gatekeeper.

**Next.js** (`/labeling`, Konva): capture, regions, versions, basename promote/rename, crops — handy when you want a screenshot-driven workflow (`uv run api` + `web` dev server).

Typical UI workflow:
1. Capture a reference screenshot (Next `/labeling`)
2. Label regions (template crop, OCR, click target, `has_red_dot`)
3. Save → updates `games/<game>/<id>/area.yaml` and `games/<game>/<id>/references/crop/`
4. Commit; use regions in analyzer YAML or DSL scenarios

#### Redis State

Multi-instance state lives in Redis:
- **`wos:instance:<id>:state`** — instance-level state hash (current_screen, paused, auto_paused, etc.)
- **`wos:queue:<id>`** — task queue (pending scenarios)
- **`wos:history:<id>`** — recent execution history
- **`wos:ui:command:<id>`** — UI → bot commands

Restart-safe; visible from the Next.js dashboard and any external tool.

### Configuration

**`src/config/settings.yaml`** — main config (YAML):
```yaml
redis:
  url: redis://127.0.0.1:6379
  db: 0

ocr:
  tesseract_cmd: /opt/homebrew/bin/tesseract

worker:
  adb_executable: /opt/homebrew/bin/adb  # or use ANDROID_HOME env var
  screenshot_interval_ms: 500
```

**Environment variables** override YAML:
- `WOS_REDIS_URL` → `redis.url`
- `WOS_TESSERACT_CMD` → `ocr.tesseract_cmd`
- `TESSDATA_PREFIX` → Tesseract traineddata path
- `WOS_ADB_PROBE_HOST` → `worker.adb_probe_host` — host the `/adb` emulator-port scan probes (default `127.0.0.1`; set to `host.docker.internal` when the API runs in a bridge-network container, paired with `ADB_SERVER_SOCKET=tcp:host.docker.internal:5037` so all `adb` calls go through the host's adb server)

**Devices + accounts in SQLite** (`db/state/state.db`):

The legacy `db/devices.yaml` is gone. Device and account state lives in SQLite
so the worker, API, and dashboard share one source of truth. Edit through the
Next.js dashboard (`/adb`, `/accounts`) — direct SQL is fine for inspection but
do not hand-edit from scripts that bypass `src/config/devices_db.py` validation.

Tables (see `src/config/devices_db.py` for the canonical schema):
- `devices` — columns: `name`, `adb_serial`, `screenshot_backend`, `input_backend`,
  `display_json`, `device_order`, `updated_at`.
- `device_profiles`, `device_profile_gamers`, `gamers` — account-to-device mapping.

Inspect with (plain SQLite — the stdlib `sqlite3` CLI works, or via Python):
```sh
uv run python -c "import sqlite3; \
  print(sqlite3.connect('db/state/state.db').execute( \
  'SELECT name, adb_serial, screenshot_backend, input_backend FROM devices ORDER BY device_order').fetchall())"
```

#### Databases (plain SQLite)

Every database the app owns (`db/state/state.db`, `src/modules/notify/data/notify_monitor.db`, the dreamscape `scenes.db`) is a **plain, unencrypted SQLite file**. Persistence goes through SQLAlchemy — `config.orm.get_engine()` (covers `state.db` + `scenes.db`) and notify's `_make_engine` — using the stdlib `sqlite3` driver in WAL mode. Open them with any SQLite tool or the stdlib `sqlite3` module.

Installs that previously ran the SQLCipher-encrypted build have encrypted `.db` files; convert them once with `uv run --with sqlcipher3-wheels python scripts/decrypt_databases.py` (worker stopped; keeps `<name>.encrypted.bak`). See [`CONTRIBUTOR.md`](CONTRIBUTOR.md).

**Per-device backend selection** (`screenshot_backend` / `input_backend` columns):

| backend     | screenshot | input | notes                                                       |
| ----------- | ---------- | ----- | ----------------------------------------------------------- |
| *(empty)*   | ✓          | ✓     | Smart default: scrcpy for every device (falls back to adb)  |
| `adb`       | ✓          | ✓     | Universal fallback (`exec-out screencap` / `input tap`)     |
| `minitouch` |            | ✓     | DeviceFarmer native input (~5-20 ms/tap; rooted only)        |
| `scrcpy`    | ✓          | ✓     | Genymobile scrcpy server: H.264 video + touch events through one device-side process. Auto-pushes `scrcpy-server.jar`. Any unrooted device. |

Whitelists are enforced in `src/config/devices_db.py:VALID_SCREENSHOT_BACKENDS`
and `VALID_INPUT_BACKENDS`. Both fields can be set via the `/adb` dropdowns or
`POST /api/adb/devices/{serial}/backend`. Worker restart required to apply.

## Cursor Rules

Two Cursor rules are applied to this repo:

1. **`python-uv.mdc`** — Always use `uv` for Python workflows (not pip, Poetry, etc.)
2. **`wos-overlay-actions.mdc`** — Overlay engine conventions (analyzer YAML vs `area.json`, DSL guards, red-dot filters, optional taps)

## Python & Dependencies

- **Python 3.13** (pinned in `.python-version`)
- **uv** for dep management + running scripts
- **pyproject.toml** defines all scripts:
  - `play` — API + Next.js production build (`next build` then `next start`); worker is opt-in via dashboard **Start bot**
  - `bot` — Headless worker + scheduler
  - `api` — FastAPI for Next.js (`web/`)

**Key dependencies:**
- `redis` — state + queue
- `httpx` — async HTTP
- `pydantic` — config validation
- `opencv-python` — image processing
- `tesseract-ocr` (system package) — local OCR
- `pyyaml` — scenario DSL parsing
- `networkx` — screen graph routing

## Testing

- **Pytest discovery:** `tests/` root + `games/<game>/*/tests/` parallel
- **Shared fixtures:** `conftest.py` (root level)
- **Run tests locally:** `uv run pytest -q`
- **Run dev tools:** `uv sync --extra dev` (installs ruff + pytest)

Module tests should use `device_level: true` fixtures to avoid Redis state pollution across test runs.

## Emulator Requirements

**Mandatory:**
- **Resolution:** 720 × 1280 (Portrait)
- **DPI:** 320
- **Game language:** English
- **ADB:** Enabled

## Common Development Tasks

### Adding a New Feature Module

1. Create `games/<game>/my_feature/` with `module.yaml`, `analyze/`, `scenarios/`, `tests/`
2. Fill in `module.yaml` (`id`, `title`, `enabled`, plus any non-default `scenarios`/`analyze`/`area`/`references` paths)
3. Define analyzer rules in `analyze/analyze.yaml`
4. Write DSL scenarios in `scenarios/*.yaml`; add an optional `exec.py` only for logic the DSL can't express
5. Run `uv run pytest games/<game>/my_feature/tests/` to test

### Creating a New DSL Scenario

1. Create `games/<game>/<feature>/scenarios/my_scenario.yaml`
2. Define steps using `match`, `click`, `while_match`, `cond`, `wait`, `push_scenario`, etc.
3. Use `.cursor/rules/wos-overlay-actions.mdc` for DSL patterns (optional taps, red-dot filters, guards)
4. Test locally: Next.js **DSL runner** (`/debug-run`), or `uv run play` / Redis CLI

### Labeling a New UI Region

1. Open Next.js **Labeling** (`http://127.0.0.1:3000/labeling` with `uv run api` + bot running)
2. Capture a reference screenshot
3. Draw region bounding boxes and label them (template crop, OCR text, click target, red-dot enabled)
4. Save → updates `games/<game>/<id>/area.yaml` and `games/<game>/<id>/references/crop/*.png`
5. Commit changes; use regions in analyzer YAML or DSL scenarios

### Debugging a Failing Scenario

1. Check `docker compose logs bot` (or Next.js **DSL runner** `/debug-run` / Streamlit debug page)
2. Look for step-level failure reason (`match_region_not_found`, `overlay_rule_timed_out`, etc.)
3. Verify region exists in `games/<game>/<id>/area.yaml` and has a crop in `games/<game>/<id>/references/crop/`
4. Re-capture reference if UI styling changed
5. Adjust threshold in analyzer rule or DSL `cond` logic

## Important Files

- **`CONTRIBUTOR.md`** — developer setup (uv, Docker, Tesseract, ADB, Web UI)
- **`web/README.md`** — Next.js dashboard (routes, env, Streamlit legacy notes)
- **`README.md`** — user docs (installation, features, emulator config)
- **`pyproject.toml`** — Python config (scripts, deps, uv sources)
- **`.cursor/rules/`** — Cursor IDE guidance (Python uv, overlay DSL patterns)
- **`area.json`** — root placeholder; live screen definitions live in `games/<game>/<id>/area.yaml` and are merged via `layout.area_manifest.load_area_doc`
- **`conftest.py`** — pytest fixtures shared across modules
