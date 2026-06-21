# Grafana Cloud telemetry

The bot emits four metrics over OTLP HTTP. Together they answer
"how many users are online", "how long their bots have been running",
"how many emulators do they drive", and "where are they failing".

| Metric                           | Type              | Labels                       | What it tells you |
| -------------------------------- | ----------------- | ---------------------------- | ----------------- |
| `autopilot.heartbeat`            | observable gauge  | `host`                       | Constant 1; `count(count by(host)([5m]))` = active machines |
| `autopilot.uptime_seconds`       | observable gauge  | `host`                       | Seconds since the bot's supervisor started |
| `autopilot.workers.active`       | observable gauge  | `host`                       | Alive worker subprocesses (scheduler excluded) |
| `autopilot.restarts`             | counter           | `process_name`               | Aggregate restart rate (no per-host breakdown) |

Labels were trimmed deliberately to keep cardinality at *one series per
host per gauge* — three series total per active machine. `version` would have
inflated each series's bytes without adding analytical value (it's constant
per host, so it doesn't help segment the heartbeat query).

Resource-level labels on every series: `service.name=wos`,
`service.namespace=wos`, `service.instance.id`, `service.version`,
`wos.component` (supervisor / worker / scheduler / api).

## One-time setup (maintainer)

1. **Get a Grafana Cloud account.** The free tier supports OTLP HTTP ingest
   directly — no collector needed: <https://grafana.com/auth/sign-up/create-user>.
2. **Open your stack** → *Connect data* → *OpenTelemetry* → *OTLP-HTTP*.
   Copy the endpoint URL and the pre-encoded `Authorization` header.
3. **Provide the creds.** They live in `src/config/_telemetry_secrets.py`
   (`ENDPOINT` + `AUTH_HEADER`). The file is **gitignored** — it must be present
   in the *Docker build context* so `COPY src/` pulls it into the bot image.
   Two ways to get it there:

   - **Official build (CI):** set repo secrets `TELEMETRY_OTLP_ENDPOINT` and
     `TELEMETRY_OTLP_AUTH_HEADER` (Settings → Secrets and variables → Actions).
     The `Bake telemetry secrets` step in `.github/workflows/docker.yml` writes
     the file from them just before `docker build`. Unset → the image ships with
     no telemetry.
   - **Local build:**
     ```sh
     cp src/config/_telemetry_secrets.py.example src/config/_telemetry_secrets.py
     $EDITOR src/config/_telemetry_secrets.py   # fill ENDPOINT + AUTH_HEADER
     ```

   > ⚠️ The file must **not** be added to `.dockerignore` — excluding it silently
   > strips it from the build context, so nothing (metrics *or* logs) is ever
   > baked or shipped.

4. **Build the production image** (CI does this on release; locally:)
   ```sh
   DOCKER_BUILDKIT=1 docker build -f Dockerfile.bot -t autopilot:latest .
   ```
5. **Import the dashboard.** In Grafana: *Dashboards* → *New* → *Import* →
   upload `grafana/autopilot_telemetry.json`. Pick your Prometheus datasource
   when prompted.

## How "active users" is computed

The bot emits `autopilot_heartbeat=1` every export interval (60 s default).
Each data point carries the machine's `host` label (its hostname).
In Grafana / Mimir / Prometheus:

```promql
count(count by(host) (autopilot_heartbeat[5m]))
```

> "Distinct hosts that sent at least one heartbeat in the last 5 minutes."

Adjust the window (`[5m]`) to taste — short windows are sensitive, long
windows hide brief outages.

## Local smoke test (no production build)

```sh
export OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp-gateway-prod-us-central-0.grafana.net/otlp"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64(instance:token)>"
uv run bot   # supervisor logs "telemetry: gauges registered"
```

Within ~60 s the dashboard's "Active users" panel should tick up to 1.
Stop the bot, wait 5 min, the panel drops back to 0.

## Mandatory by design

End users cannot opt out of telemetry in production builds:

- `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_HEADERS` — overwritten
  unconditionally by the baked values at process start (see
  `runtime_bootstrap._apply_baked_telemetry_secrets`).
- `OTEL_SDK_DISABLED=true` — stripped from the env before the SDK reads it.
- `OTEL_METRICS_EXPORTER=none` — stripped, so metrics always export.
- **Logs** are forced on at `ERROR` only (`WOS_OTEL_LOG_LEVEL=ERROR`,
  `OTEL_LOGS_EXPORTER` cleared): uncaught exceptions / crashes and `ERROR`-level
  lines ship to Loki with stack traces. Requires the OTLP token to carry
  `logs:write` scope (metrics-only tokens 401 on log export). `INFO`/`DEBUG`
  never leave the machine.
- `OTEL_TRACES_EXPORTER=none` — traces stay off (no per-span egress).

For dev builds (public repo, no `_telemetry_secrets.py`) the function is a
no-op, so contributors don't accidentally ship metrics during local work.

The bot will keep running even if telemetry export fails — the gauges /
counters are wrapped in defensive try/except and export errors stay in
debug logs, so a flaky network doesn't take down the worker.

## Privacy notes

Labels carry only the machine hostname (`host`). No gameplay data, no
screenshots, no ADB output — only the metrics listed in the table above. If you
need to anonymize, hash `host` in `_common_attributes` (see
`src/config/telemetry.py`).
