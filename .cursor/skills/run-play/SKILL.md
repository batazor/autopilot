---
name: run-play
description: Run the bot. Use when the user says to start/run the bot.
---

# Run bot

**Local dev (worker + API + Next.js dashboard):**

```bash
docker compose up -d redis   # if needed
uv run play
```

Opens http://127.0.0.1:3000/overview (API :8765). Worker is **not** started by default — use **Start bot** in the sidebar.

**Headless worker only (no UI):**

```bash
uv run bot
```

**Legacy Streamlit all-in-one** (duplicate UI on :8501):

```bash
WOS_PLAY_STREAMLIT=1 uv run play
```

**Split terminals** (same as before): `uv run bot`, `uv run api`, `cd web && npm run dev`.

Optional env: `WOS_PLAY_NO_WEB=1`, `WOS_PLAY_NO_API=1`, `WOS_PLAY_OPEN_BROWSER=0`, `WOS_FORCE_RESTART=1`.
