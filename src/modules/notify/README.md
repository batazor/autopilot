# notify_monitor

A notification monitoring service for **Whiteout Survival** (`com.gof.global`)
and **Kingshot** (`com.run.tower.defense`). It polls Android notifications over
ADB, parses them, matches each against per-game event patterns, **publishes
recognized events to Redis**, and **stores unrecognized ones in SQLite** for
review. Everything (patterns, players, settings) is manageable from a small web
UI — no code edits needed.

## Run

```sh
# Redis must be running (docker compose up -d redis), a device connected (adb devices)
uv run python -m modules.notify                 # -> http://127.0.0.1:8800
uv run python -m modules.notify --port 8800 --reload   # dev auto-reload
```

The single process runs **both** the web UI and the background poller (started
in the FastAPI lifespan hook). Open the URL and use the tabs:

- **Dashboard** — counts, Redis publish status, live event feed, *Poll now*
- **Players** — add/remove/toggle monitored players (also auto-discovered)
- **Patterns** — add/edit/delete/toggle patterns, **test a regex** against sample text
- **Unrecognized** — mark reviewed or **promote** to a new pattern
- **Settings** — polling interval, ADB serial/path, enable/disable monitor

## API examples

These examples target the standalone notify service. When using the main web
API, use the same suffixes under `/api/notify/...`.

```sh
# Inspect current monitor state and connected ADB devices.
curl -s http://127.0.0.1:8800/api/status

# Trigger one poll cycle now.
curl -s -X POST http://127.0.0.1:8800/api/poll

# Register a player explicitly; players are also auto-discovered from text.
curl -s -X POST http://127.0.0.1:8800/api/players \
  -H 'Content-Type: application/json' \
  -d '{"nickname":"batazor","game":"wos","active":true}'

# Add a recognition-only pattern.
curl -s -X POST http://127.0.0.1:8800/api/patterns \
  -H 'Content-Type: application/json' \
  -d '{"game":"wos","event_type":"trek_supply","pattern_regex":"trek supplies?.*(ready|claim)","description":"Trek supplies ready to claim"}'

# Add a pattern that pushes a DSL scenario when it matches.
curl -s -X POST http://127.0.0.1:8800/api/patterns \
  -H 'Content-Type: application/json' \
  -d '{"game":"wos","event_type":"intel_lighthouse","pattern_regex":"(intel|lighthouse).*(new|intel|check)","description":"New Intel in the Lighthouse","scenario":"intel_lighthouse"}'

# Test a regex before saving it.
curl -s -X POST http://127.0.0.1:8800/api/patterns/test \
  -H 'Content-Type: application/json' \
  -d '{"pattern_regex":"offline\\s+income.*(max|maxed|claim|ready)","sample_text":"Claim Offline Income — Honored batazor, offline Income is maxed out. Come and claim it!"}'
```

## How it works

```
ADB dumpsys notification  ─►  parser  ─►  PatternMatcher (hot-reload from SQLite)
                                              │
                    recognized ───────────────┼──────────── unrecognized
                          ▼                                       ▼
   Redis publish  {game}:events:{nickname}        SQLite unrecognized_notifications
   + SQLite events log                                (reviewed flag, promote → pattern)
```

- **Redis channel:** `wos:events:{nickname}` / `kingshot:events:{nickname}`
  with payload `{game, player, event_type, raw_text, timestamp}`.
- **Nickname** resolution: a pattern's `(?P<nickname>...)` group → a known
  monitored player found in the text → leading-`[bracket]`/`Name:` heuristics →
  `"unknown"`.
- **Deduplication:** each notification is hashed (`pkg|raw_text`); the same one
  is processed at most once per ~10 min window (and never twice in one cycle).
- **Hot-reload:** pattern edits in the UI take effect within seconds — the
  matcher reloads from SQLite (no restart). Poll interval / ADB serial are read
  fresh each cycle.
- **Seed sync:** default patterns are inserted on boot only when a `(game,
  event_type)` pair is missing. Existing operator edits stay intact. The current
  seeds include live notification examples such as Storehouse Supply, Offline
  Income, Trek Supplies, Secured Alliance Gathering Node, and Kingshot Sanctuary
  Battle.

## Modules

| module            | responsibility                                          |
| ----------------- | ------------------------------------------------------- |
| `config.py`       | game registry (packages), seed patterns, defaults       |
| `adb_reader.py`   | `adb shell dumpsys notification --noredact`             |
| `parser.py`       | dumpsys parsing, nickname extraction, pattern matching  |
| `publisher.py`    | Redis event publisher + status                          |
| `db.py`           | SQLite layer (players, patterns, events, unrecognized, settings) |
| `service.py`      | background poll loop + dedup + dispatch                 |
| `app.py`          | FastAPI web UI + JSON API                               |
| `static/index.html` | single-page UI                                        |

## Config (env vars, all optional)

| var             | default                          | meaning                       |
| --------------- | -------------------------------- | ----------------------------- |
| `NM_REDIS_URL`  | `redis://127.0.0.1:6379/0`       | Redis connection              |
| `NM_DB_PATH`    | `src/modules/notify/data/notify_monitor.db` | SQLite file            |
| `NM_LOG_PATH`   | `src/modules/notify/data/notify_monitor.log` | log file (also console) |
| `NM_ADB_PATH`   | `adb`                            | adb binary                    |
| `NM_HOST`/`NM_PORT` | `127.0.0.1` / `8800`         | web server bind               |

Runtime settings (poll interval, ADB serial, enabled) live in the SQLite
`settings` table and are edited from the **Settings** tab.

## Tests

```sh
uv run pytest src/modules/notify/tests/ -q
```
