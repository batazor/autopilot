# VIP Module

Owns the VIP reward claim flow.

Current module scope:

- `vip.claim` refreshes `page.vip.level`, claims VIP box rewards, daily add rewards, and VIP unlock rewards when their red-dot indicators are present.

Module-local scenarios live in `scenarios/`; overlay/analyze fragments live in `analyze/analyze.yaml`.

Components can now move in gradually:

- `area.yaml` may define module screens/regions. Relative `ocr` paths resolve from this module root.
- `references/` may hold module screenshots.
- `references/crop/` may hold module crop templates for those screenshots.

Navigation edges are still global in `navigation/`.
