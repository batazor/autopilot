# Grafana Cloud telemetry

The bot emits four metrics over OTLP HTTP. Together they answer
"how many users are online", "how long their bots have been running",
"how many emulators do they drive", and "where are they failing".

| Metric                           | Type              | Labels                       | What it tells you |
| -------------------------------- | ----------------- | ---------------------------- | ----------------- |
| `autopilot.heartbeat`            | observable gauge  | `sub`                        | Constant 1; `count(count by(sub)([5m]))` = active users |
| `autopilot.uptime_seconds`       | observable gauge  | `sub`                        | Seconds since the bot's supervisor started |
| `autopilot.workers.active`       | observable gauge  | `sub`                        | Alive worker subprocesses (scheduler excluded) |
| `autopilot.restarts`             | counter           | `process_name`               | Aggregate restart rate (no per-user breakdown) |
| `autopilot.license.gate_failures`| counter           | `fingerprint`, `reason`      | Bots that refused to start because of a license problem |

Labels were trimmed deliberately to keep cardinality at *one series per
user per gauge* — three series total per active user. `tier` / `version`
/ `fingerprint` would have inflated each series's bytes without adding
analytical value (they're constant per user, so they don't help segment
the heartbeat query). If you need tier/version segmentation later, add a
lower-frequency `autopilot.license.session` counter rather than fattening the
high-frequency gauges.

Resource-level labels on every series: `service.name=wos`,
`service.namespace=wos`, `service.instance.id`, `service.version`,
`wos.component` (supervisor / worker / scheduler / api).

## One-time setup (maintainer)

1. **Get a Grafana Cloud account.** The free tier supports OTLP HTTP ingest
   directly — no collector needed: <https://grafana.com/auth/sign-up/create-user>.
2. **Open your stack** → *Connect data* → *OpenTelemetry* → *OTLP-HTTP*.
   Copy the endpoint URL and the pre-encoded `Authorization` header.
3. **Drop the creds into the repo:**
   ```sh
   cp src/config/_telemetry_secrets.py.example src/config/_telemetry_secrets.py
   $EDITOR src/config/_telemetry_secrets.py   # fill ENDPOINT + AUTH_HEADER
   ```
   The real file is gitignored. When the production Docker build runs Nuitka,
   the two strings get absorbed into `config.so` and ship to users.
4. **Build the production image:**
   ```sh
   DOCKER_BUILDKIT=1 docker build -f Dockerfile.bot -t autopilot:latest .
   ```
5. **Import the dashboard.** In Grafana: *Dashboards* → *New* → *Import* →
   upload `grafana/autopilot_telemetry.json`. Pick your Prometheus datasource
   when prompted.

## How "active users" is computed

The bot emits `autopilot_heartbeat=1` every export interval (60 s default).
Each data point carries the user's `sub` label (their email from the license).
In Grafana / Mimir / Prometheus:

```promql
count(count by(sub) (autopilot_heartbeat[5m]))
```

> "Distinct subjects that sent at least one heartbeat in the last 5 minutes."

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
- `OTEL_METRICS_EXPORTER=none` / `OTEL_TRACES_EXPORTER=none` — same treatment.

For dev builds (public repo, no `_telemetry_secrets.py`) the function is a
no-op, so contributors don't accidentally ship metrics during local work.

The bot will keep running even if telemetry export fails — the gauges /
counters are wrapped in defensive try/except and export errors stay in
debug logs, so a flaky network doesn't take down the worker.

## Privacy notes

Labels carry the user's email (`sub`) and a hashed machine fingerprint. No
gameplay data, no screenshots, no ADB output — only the metrics listed in
the table above. If you need to anonymize, hash `sub` before passing it to
`bind_license_claims` (see `src/config/telemetry.py`).
