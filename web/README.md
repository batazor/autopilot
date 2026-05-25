# WOS Web UI (Next.js)

Primary **operator dashboard** (local dev and production Docker). The app talks to Redis and the bot through a **FastAPI** layer (`uv run api`); Next.js proxies `/api` to that service.

Stack: **[Tailwind CSS v4](https://tailwindcss.com/docs/installation/framework-guides/nextjs)** (PostCSS in Next.js), **[Headless UI](https://headlessui.com/)** (accessible Listbox / Dialog), **[react-select](https://react-select.com/)** (searchable / creatable fields in scenario editor).

**All operator pages** live in this Next.js app (see [Pages](#pages) below). **Production Docker** (`docker-compose.prod.yml`) serves this UI on **:3000** with FastAPI on **:8765** (see [README](../README.md#-installation--setup)).

## Prerequisites

| Tool | Notes |
|------|--------|
| [uv](https://docs.astral.sh/uv/) | `uv sync` at repo root |
| Node.js 20+ | `npm install` in `web/` |
| Redis | `docker compose up -d redis` |
| Running bot | `uv run play` (worker + API + Next.js) **or** `uv run bot` (headless worker only) |

## Run

**One command** (recommended):

```sh
cd /path/to/autopilot
uv sync
docker compose up -d redis   # if not already running
uv run play                  # worker + scheduler + API (:8765) + Next.js (:3000)
```

Open http://127.0.0.1:3000/overview (redirects from `/`).

**Theme:** dark is the default (ops). Use **Light** in the sidebar footer (or mobile header) for daytime work and wiki screenshots; choice is stored in `localStorage` (`wos-theme`).

**Split terminals** (optional):

```sh
uv run bot                   # headless worker + scheduler only
uv run api                   # FastAPI on :8765
cd web && npm install && npm run dev
```

**Styles** are split under `web/app/styles/` (see `web/app/styles/README.md`). Entry: `web/app/globals.css`.

**Styles look unstyled (plain links, serif font)?** Stop the dev server, clear the Next cache, and restart from `web/`:

```sh
rm -rf .next
npm run dev
```

Then hard-refresh the browser (Cmd+Shift+R). This usually happens after a failed compile left `.next` in a bad state.

## Pages

All routes below are implemented in Next.js.

| Route | Group | Description |
|-------|-------|-------------|
| `/overview` | Operate | Fleet metrics, pause/resume |
| `/instance` | Operate | Rolling preview, manual commands, history |
| `/player-state` | Operate | Redis live state, `state.yaml`, Century sync |
| `/approvals` | Debug | Click approval gate (live approve/reject) |
| `/overlay-test` | Debug | Overlay rule match debugger |
| `/queue` | Debug | Pending / running / history |
| `/debug-run` | Debug | DSL runner (enqueue module scenario) |
| `/routes` | Debug | Screen route planner + edge table |
| `/optimizer` | Debug | Queue optimizer debug |
| `/gallery` | Assets | Redirects to `/labeling` |
| `/labeling` | Assets | **Konva** editor: regions, versions, basename promote, capture, crops |
| `/edit-dsl` | Assets | Module DSL editor (form + YAML preview) |
| `/analyze` | Assets | Analyzer / overlay rules viewer |
| `/wiki` | Assets | Wiki reference (buildings, heroes, items, gear, FAQ); sync scripts with progress on FAQ |
| `/gift-codes` | Assets | Promo codes: table, scrape, redeem |
| `/modules` | Config | Module catalog + scenario enable/disable |
| `/adb` | Config | ADB devices |
| `/balance` | Config | Resource balance view |

**Labeling** ([Konva](https://konvajs.org/) at `/labeling`): draw/move/resize regions, metadata, save `area.json` (base or `versions[vN].regions[]`), capture/refresh/discard, basename **promote** from `temporal/`, **rename**, version cond/sync/bind.

**Wiki FAQ sync** (`/wiki` → FAQ tab): run sync scripts with live log + progress (`WikiFaqSync`).

**Rehearsal** (queue, click approvals, overlay probe): `/approvals`, `/queue`, `/overlay-test` (requires `uv run api` + a running worker).

## UI components

| Component | Path | Use |
|-----------|------|-----|
| `AppListbox` | `components/headless/AppListbox.tsx` | Toolbar / simple dropdowns ([Headless UI Listbox](https://headlessui.com/react/listbox)) |
| `AppConfirmDialog` | `components/headless/AppConfirmDialog.tsx` | Confirm destructive actions ([Dialog](https://headlessui.com/react/dialog)) |
| `AppSelect` | `components/AppSelect.tsx` | Searchable selects (react-select); non-searchable mode delegates to `AppListbox` |

## Architecture

```text
Browser (:3000)  →  Next.js  →  /api/*  →  FastAPI (:8765)  →  Redis / filesystem / bot commands
Bot worker       →  Redis (queue, state, previews)
```

## Env

| Variable | Default | Purpose |
|----------|---------|---------|
| `WOS_API_PORT` | `8765` | FastAPI listen port |
| `WOS_API_URL` | `http://127.0.0.1:8765` | Next.js rewrite target (`web/next.config`) |
| `WOS_PLAY_NO_WEB` / `WOS_PLAY_NO_API` | unset | Skip Next.js or FastAPI child (bot still runs) |
