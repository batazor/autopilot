# Who am I module

Device-level bootstrap scenario **`who_i_am`**: reads the in-game player id from
**chief_profile**, calls Century `fetch_player`, and writes **`active_player`**
on the instance before any player-bound work is attributed.

## Scenario

| Key | Priority | When |
|-----|----------|------|
| `who_i_am` | 82_000 | `active_player == ""` (also seeded at worker boot) |

Screen identity is maintained by the worker's rolling detector, so this scenario
only bootstraps `active_player`. It stays **above** routine overlays (70k–80k).

## Layout

Uses core `area.json` and `references/` (see `module.yaml`). No module-local
analyze manifest — identity is DSL + OCR only.
