# Exploration Module

Owns the Exploration flow and its Squad Settings battle loop.

Current module scope:

- `claim_exploration_rewards` claims available Exploration rewards on a cron cadence.
- `squad_fight` reads the squad matchup, deploys, fights, polls victory/defeat, and requeues itself after victories.

Module-local scenarios live in `scenarios/`; overlay/analyze fragments live in `analyze/analyze.yaml`.

Components can now move in gradually:

- `area.yaml` may define module screens/regions. Relative `ocr` paths resolve from this module root.
- `references/` may hold module screenshots.
- `references/crop/` may hold module crop templates for those screenshots.

Navigation edges are still global in `navigation/`.
