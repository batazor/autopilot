MCP rehearsal captures for exploration scenarios.

## Layout

- **`*.rehearsal.*.png`** — per-step MCP dumps (gitignored).
- **`fixtures/<scenario>/`** — minimal committed frames for pytest (e.g. `claim_exploration_rewards/01.main_city_before.png`).

Production screen references for exploration-only screens (`page.exploration.victory.png`, `page.exploration.defeat.png`, `page.squad_fight.png`) stay in `references/` at the module root. The shared `page.rewards.png` lives in the dedicated [`modules/core/rewards`](../../../rewards) module.
