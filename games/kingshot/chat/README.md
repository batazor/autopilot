# Kingshot chat — alliance broadcast (scaffold)

This module wires Kingshot into the game-agnostic alliance-broadcast core
(`src/modules/broadcast/`). The catalog, selection engine, broadcaster election,
cooldown/claim de-duplication, API, and dashboard all already cover Kingshot
(message scope `kingshot` or `all`). **Only on-device delivery is deferred.**

## What works today

- `exec.py` registers `alliance_broadcast_tick` for `game="kingshot"`.
- `scenarios/broadcast_tick.yaml` cron-drives it every 15 min.
- Messages scoped `kingshot`/`all` are selected and the broadcaster is elected.

The tick reaches step 4 (navigate to `chat.alliance`) and then fails cleanly
(`result.action == "nav_failed"`) because the chat screen graph isn't built yet.

## To light it up (needs a live Kingshot device)

1. Label the chat screen on a 720×1280 Kingshot client:
   - add `routes/screen_verify.yaml` + `routes/edge_taps.yaml` for the
     `chat` / `chat.alliance` nodes and the `main_city → chat` entry button
     (mirror `games/wos/chat/routes/`);
   - capture a reference screenshot and replace the placeholder `area.yaml`
     `chat.alliance.input` / `chat.alliance.send` bboxes with real ones (add the
     matching crop tiles under `references/crop/`).
2. Re-tune the fallback tap percentages in
   `src/modules/broadcast/runner.py` if Kingshot's input bar differs from WoS.

No core changes are required — the wrapper "lights up" the moment the chat node
becomes routable.
