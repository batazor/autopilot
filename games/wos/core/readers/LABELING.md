# Blind-planner readers — on-device labeling handoff

Five investment planners (pets / charms / gear / hero_gear / island) were blind for
lack of an on-device reader. The **reader code, persistence, and planner read-back
wiring are built, wired, and unit-tested** (`games/wos/core/readers/` + each
module's `exec.py`). What remains is **on-device labeling** (and, for three of
them, a navigation edge) — the part that needs a real device + the standard Next
`/labeling` capture→draw→crop→save flow. Each reader's cron ships `enabled: false`
until its screen is reachable and its cells are labeled; flipping it on then makes
the planner go live automatically.

## The convention (all 5 readers)

The cron navigates to the screen, OCRs each labeled cell into a Redis field named
`<domain>.read.<entity>[.<stat>]` (the `store:` target of an `ocr:` step), then
calls `exec: sync_<reader>`. The handler collects every `<domain>.read.*` field,
assembles the `owned` dict via a pure (fixture-tested) parser, and persists it to
durable SQLite + the hot Redis mirror. So labeling = add one `ocr: … store:
<domain>.read.<entity>…` step per cell, then `enable: true`.

Standard OCR for small level badges: `type: integer`, `preprocess: fast_digits`,
`threshold: 0.0` (confidence is ~0 even when correct — see `sync_furnace_level`).

## Per reader

### 🐾 pets — `games/wos/pets/` (screen already navigable: main_city→pets)
Only labeling. Cron: `scenarios/by_cron/sync_pet_owned.cron.yaml`.
1. Confirm on-device: does a Pet-Hall roster show every owned pet's level (+ refine
   / skill) on one screen, or only per-pet? If per-pet, loop the cards like
   `sync_hero_roster`'s chevron walk.
2. Add a `pets.roster` screen node + `pets→pets.roster` edge if it's a separate screen.
3. Per pet: `ocr: … store: pets.read.<pet_id>.level` (+ `.refine`, `.skill`).
4. `enable: true`.

### 🌳 island — `games/wos/core/island/` (screen node + 'My Island' verify exist)
Labeling **+ a nav gap**. Cron: `scenarios/by_cron/sync_island_state.cron.yaml`.
1. **NAV** — the only island edge today is `island→main_city` (return). Entry is a
   one-shot panel exec tap, so a cron with `node: island` has no routable path TO
   it. Add a re-entry edge (or an exec-tap entry step) first.
2. Label + `ocr:` steps: `island.read.tree_of_life_level`, `…prosperity`,
   `…life_essence`; decorations as `island.read.decoration.<id>`; lumber camps as
   `island.read.lumber.<n>`.
3. `enable: true`.

### 🔮 charms — `games/wos/core/charms/` (greenfield: nav + labeling)
1. **NAV** — reach the Chief Charms screen (likely from chief_profile). Add a
   `screen_verify` (OCR title landmark) + `chief_profile→chief_charms` edge in
   `routes/`; set `node:` in the cron.
2. Label the 18 slot levels: `ocr: … store: charms.read.<slot_id>` for
   `infantry_1..infantry_6`, `lancer_1..lancer_6`, `marksman_1..marksman_6`.
3. `enable: true`.

### 🛡️ gear — `games/wos/core/gear/` (greenfield: nav + labeling)
1. **NAV** — reach the Chief Gear screen; add `screen_verify` + `chief_profile→chief_gear`
   edge; set `node:`.
2. **DECIDE** during labeling: does the screen show a NUMBER or a quality BADGE
   (e.g. "Pink T3-4")? If a number → `ocr: … store: gear.read.<piece_id>` for the 6
   pieces (`gloves_belt`/`goggles_boots` × `infantry`/`lancer`/`marksman`). If a
   badge → OCR the label and decode it to the ordinal via `db/chief_gear.yaml`'s
   ladder order (see `exec.py` NOTE).
3. `enable: true`.

### ⚔️ hero_gear — `games/wos/core/hero_gear/` (greenfield, deepest nav)
1. **NAV** — the screen opens from a hero detail card (`page.heroes.unit`). FIRST
   confirm: one 6×3 grid, or 6 per-piece sub-screens (a walk)? Add the
   `screen_verify` + `page.heroes.unit→hero_gear` edge; set `node:`.
2. Label the 18 cells: `ocr: … store: hero_gear.read.<piece_id>.<track>` for the 6
   pieces × 3 tracks (`enhance` / `mastery` / `widget`).
3. `enable: true`.

## Verifying a reader once labeled
```sh
uv run botctl drive sync_<reader>.cron --inst <inst> --player <fid> --no-approval --auto-pause
uv run botctl reader-health --inst <inst>     # the fact should flip BLIND → ok
```
