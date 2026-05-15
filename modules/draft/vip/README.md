# VIP Module

Owns the VIP reward claim flow.

Current module scope:

- `vip.claim` refreshes `page.vip.level`, claims VIP box rewards, daily add rewards, and VIP unlock rewards when their red-dot indicators are present.

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
