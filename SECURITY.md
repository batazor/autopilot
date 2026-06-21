# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

Report privately through one of:

- GitHub's [private vulnerability reporting](https://github.com/batazor/autopilot/security/advisories/new) (preferred), or
- email **batazor111@gmail.com** with `[SECURITY]` in the subject.

Include enough detail to reproduce: affected version/commit, environment, and a
proof of concept if you have one. We aim to acknowledge reports within a few
days and will keep you updated on a fix.

## Scope

This is a hobby/community project with no commercial guarantees. The most
relevant areas:

- The FastAPI server (`src/api/`) and Next.js dashboard (`web/`) — they expose
  local control of devices; treat the dashboard as a trusted-LAN tool, not an
  internet-facing service.
- Stored credentials: farm-account passwords live in the local SQLite
  `db/state/state.db` (gitignored). Never commit a populated state DB.
- Outbound telemetry: see [`grafana/README.md`](grafana/README.md) for exactly
  what a production build sends.

## Out of scope

- Bans or penalties from the target game for using automation (see the
  disclaimer in [`README.md`](README.md)).
- Vulnerabilities in third-party dependencies — report those upstream.
