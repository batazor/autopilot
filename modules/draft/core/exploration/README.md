# Exploration (core module)

Owns the Exploration flow and Squad Settings battle loop under ``modules/core/exploration/``.

Current module scope:

- `claim_exploration_rewards` claims available Exploration rewards on a cron cadence.
- `squad_fight` reads the squad matchup, deploys, fights, polls victory/defeat, and requeues itself after victories.

Module-local scenarios live in `scenarios/`; overlay/analyze fragments live in `analyze/analyze.yaml`.

Components can now move in gradually:

- `area.yaml` may define module screens/regions. Relative `ocr` paths resolve from this module root.
- `references/` may hold module screenshots.
- `references/crop/` may hold module crop templates for those screenshots.
- `wiki/{heroes,buildings,items}/` may contribute hand-authored entries that
  merge into the **DB · Wiki reference** UI alongside `db/<entity>/`. See
  [`modules/README_WIKI.md`](../README_WIKI.md) for the schema. Module rows
  override core entries with the same `id` and show a "📦 owned by module"
  badge in tiles/cards.

Navigation edges are still global in `navigation/`.
