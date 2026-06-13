---
name: dsl-scenarios
description: Author, edit, debug DSL scenarios (YAML files under scenarios/). Use when adding a new scenario, modifying an existing one, debugging why a scenario doesn't fire / loops / skips steps, or designing while_match / match / ocr / loop logic. Covers schema, step semantics, idioms, and known gotchas. Trigger words — scenario, while_match, match, ocr, dsl step, area.yaml, regions.
---

# DSL scenarios — author & debug

YAML "programs" the bot executes inside the game: a sequence of UI actions (taps, swipes, OCR reads, image probes) plus control flow (loops, conditionals).

This skill is the stable semantics + idioms + gotchas. For exact paths, file names, and current tooling, **read CLAUDE.md and the code** — those move; the rules below don't.

## Where things live

- **Runnable YAML** lives under each module's `scenarios/` tree (per-game module layout — see CLAUDE.md). Filename (sans `.yaml`) is the scenario key; `by_cron/` subdirs are cron-only; `drafts/` is ignored by the resolver. `{placeholder}.yaml` is the template form (resolved per-target, e.g. `level_up_{hero}.yaml` → `level_up_chenko`).
- **Regions** (bbox + per-region action metadata) live in each module's `area.yaml`, with reference crops under `references/crop/`. Region names are **globally unique**; `screen_id` is for screen detection, not a namespace for DSL lookup. **NEVER hand-edit `area.yaml` or its crops — only via the Labeling UI** (rule in CLAUDE.md). Hand-editing desyncs the bbox from its crop and causes format churn.
- **Overlay rules** live in module-local `analyze/analyze.yaml` (merged at runtime). Analyzer YAML uses `findIcon`; `area.yaml` uses editor action `exist` — same overlay action internally.
- **Schema is the authority:** `dsl/dsl_schema.py` (`DslScenario`, `DslStep`; models use `extra="allow"`, so unknown keys round-trip unvalidated). The `dsl_*_mixin.py` executors are runtime truth — **if runtime and schema disagree, the runtime wins.** When unsure about a key or modifier, grep the schema/executor rather than guessing.

## Top-level scenario keys

```yaml
name: "Human-readable label"          # required
enabled: true                          # default false → scenario ignored at load
device_level: true                     # popup-style, no player binding (see below)
priority: 80_000                       # queue priority, default DEFAULT_SCENARIO_PRIORITY (80_000)
node: mail                             # node graph target to navigate to before running
cron: "0 */1 * * *"                    # cron schedule (player-bound scenarios only)
cond: "currentNode == main_city"       # scenario-level guard, evaluated before steps
icon: "scenarios/icons/foo.png"        # optional UI icon for ops dashboard
steps: [...]                           # the actual program
```

New scenarios ship `enabled: true` by default (project convention); keep `enabled: false` only when running it now would misbehave (e.g. it would enqueue consumer scenarios that don't exist yet).

**`device_level: true`** means the scenario is a generic UI dismissal (popups, reconnect prompts) — no player identity needed, zero iterations of `while_match` is "ok, nothing to do." All scenarios default to **1** initial-probe attempt for `while_match`; opt in to more via `retry: { attempts: N, interval: 500ms }` when the UI needs time to settle after navigation.

## Step types

Each step carries **exactly one** action key (the validator enforces this), plus optional `cond` and modifiers.

| Key                | Purpose                                                              |
|--------------------|----------------------------------------------------------------------|
| `click`            | Tap a region (uses bbox center; respects per-region `_search`)       |
| `long_click`       | Long-press                                                           |
| `match`            | Probe a region; bare miss aborts the whole scenario                  |
| `while_match`      | Probe, run `steps:` while matched, exit when not                     |
| `ocr`              | OCR the region; persist via `store:` or `state:`                     |
| `swipe_direction`  | Swipe `direction: up\|down\|left\|right`, `delta: <px>`              |
| `push_scenario`    | Enqueue another scenario (by key, or `{scenario, ...}` dict)         |
| `exec`             | Run an in-process action by name                                     |
| `wait`             | Sleep; accepts `0.5`, `"500ms"`, `"3s"`                              |
| `ttl`              | Early-exit, reschedule self for `now + ttl` (e.g. `"30m"`, `"2h"`)   |
| `repeat` / `loop`  | Loop with `max:` / `until_match:` / `until_any_match:`; `loop` is canonical and supports `break: loop` |
| `break`            | Exit nearest `loop:` / `repeat:`                                     |
| `system_back`      | Press Android system Back when set to `true`                         |

For conditional branching, use composite `cond:` blocks (below) — there is no separate `if:` step.

**Action-less forms** (no action key) — both valid:
- **Composite `cond` block** — `cond:` + `steps:`. Runs the inner steps only when the condition holds.
- **Bare group** — only `steps:`. Inlines the inner steps (handy with YAML anchors).

## Key idioms

**Guard pattern** — "run inner steps only if region is visible":

```yaml
# Soft, guarded block (preferred for "maybe-visible" elements).
- match: button.tap_anywhere_to_exit
  threshold: 0.7
  steps:
    - click: button.tap_anywhere_to_exit
    - wait: 0.5s
  else:                          # optional
    - wait: 0.2s
```

```yaml
# Or loop-style (run inner once if visible).
- while_match: button.claim
  max: 1
  steps:
    - click: button.claim
    - wait: 0.8s
```

**`match:` semantics depend on whether `steps:`/`else:` is present:**
- **Bare `match:`** (no `steps:`/`else:`) — hard gate. Miss aborts the scenario with `match_guard_failed`. Use for "this region MUST be present, else abort."
- **`match: + steps:` / `+ else:`** — soft, guarded block. Matched → run `steps`. Miss → run `else` (if any) and continue to the next sibling step. Never aborts on miss. Use when "maybe present" is normal.

`while_match / max: 1` ≈ `match + steps`; either is fine. `while_match` makes sense when the inner `steps:` may re-trigger the match (the loop drains duplicates).

**`ttl:` step** — exits early and reschedules for `now + ttl`. Use inside `else:` to back off when a popup never appears. Accepts `30m`, `2h`, `30s`, `500ms`, raw float seconds.
```yaml
- while_match: button.tap_anywhere_to_exit
  steps:
    - click: button.tap_anywhere_to_exit
  else:
    - ttl: 30m
```

**Fallback with `else:`** — runs only when `while_match` had **zero iterations** (region never seen). On a player-bound `while_match`, providing `else:` **bypasses** the strict-reschedule path (the scenario declared its own no-match handling).

**Strict gate** — opt into "this step MUST have done work" on a player-bound scenario:
```yaml
- while_match: page.worker.add
  strict: true
  steps: [...]
```
Zero iterations after initial-probe retries → soft-fail + reschedule. Default is **non-strict** (steps are OR-semantics across the scenario) — non-strict is the default, so never write `strict: false`. Don't add `strict: true` reflexively.

**Match-step modifiers** (on both `match:` and `while_match:`):
- `threshold: 0.95` — template-match score; default `0.9`. Tighten for crowded screens.
- `min_match_saturation: 48` — reject low-saturation matches (kills grey-on-grey false positives).
- `isRedDot: true|false` — gate on the red-dot color check at the region.
- `isTabActive: true|false` / `isWhiteBorder: true|false` — gate on those visual markers.

**Retry override** on `while_match`:
```yaml
- while_match: page.foo
  retry: { attempts: 3, interval: 250ms }   # interval also accepts "0.5s" / raw seconds
```

## `ocr:` step

`ocr:` takes the **region name as a scalar string** (not a dict); persistence + parsing go in sibling keys. A nested `ocr: {region: ..., store: ...}` mapping is **rejected by the schema**.

```yaml
- ocr: intel.stamina        # region to read (or use a `region:` sibling)
  store: stamina            # Redis player-state field
  type: integer             # integer → digits only; default string → raw text
  preprocess: fast_digits   # OCR pipeline; falls back to the region's own preprocess
```

Two persistence destinations, very different lifetimes:
- `store: field` → **Redis player state** (default scope `player`; or instance state with `scope: instance`). Also auto-writes siblings `<field>_text`, `<field>_confidence`, and `<field>_at` (epoch seconds of the read — use it for staleness / interpolation). Declared `store:` fields are `HDEL`'d at the start of the scenario run that re-stores them — ephemeral.
- `state: dotted.path` → the long-lived **SQLite player-state DB** (SQLCipher-encrypted — *not* a YAML file). Survives restarts; for durable "facts about the player" and drives arithmetic `cond` via the flat state dict.
- Both absent → defaults to `store: <region>`. The legacy `to_state:` key is gone.

## Cond expressions

Used at scenario-level and step-level (`cond: ...`).

- `currentNode == main_city` / `!=` — node graph state.
- `<field> == "value"` / `!=` — full-string, case-insensitive.
- `<field> ~= "Upgrade|Build"` — case-insensitive substring; `|` is alternation.
- `<field> == null` / `!= null` — "field empty/unset" (also `nil`, `none`, `empty`).
- **Numeric / arithmetic** comparisons too: `stamina >= 10`, `a.power * 1.2 >= b.power`. Use Python operators (`and`/`or`, `>=`), not SQL `AND`. Note: `"true"`/`"false"` strings are NOT coerced to bool (only numeric strings are) — store boolean flags as `1`/`0`.
- LHS is a state field looked up **player-scoped first** (where OCR `store:` lands), then falling back to instance state (`active_player`, `current_screen`, DSL bookkeeping).
- A player-bound scenario (no `device_level`) already implies an active player — **don't** add a redundant `cond: "active_player != null"`.

## Region naming

- Reusable buttons: `button.<name>` (`button.claim`, `button.close`, `button.tap_anywhere_to_exit`).
- Region unique to one screen/page: `<pagename>.<name>` (`mail.title`, `alliance.main_city.new_alliance.mail.close`).
- Page title/header landmark: `<pagename>.title`.
- Detector aux regions: primary `<name>`, search window `<name>_search`, tap target `<name>_tap`.
- Don't reuse a short name across unrelated screens for a page-specific element — prefer the page prefix so YAML and overlay logs are unambiguous. `screen_id` does NOT scope duplicate names; DSL lookup is by global region name.
- If a crop visually represents a button but OCR returns empty text, use `action: exist` and a button-style name, not `action: text`.

## Authoring checklist

1. Classify the shape: overlay-triggered, cron/player-bound, device-level popup, or navigation/helper.
2. Write the YAML under the right module's `scenarios/`; the filename (sans `.yaml`) is the scenario key.
3. Label any new regions via the Labeling UI (never hand-edit `area.yaml`). Reuse an existing `node:` when navigation already knows it.
4. Compose with **guards**: `match + steps` or `while_match / max: 1` for optional UI. Never use bare `match:` for a maybe-absent element.
5. Add a regression test next to the module (fake-actions replay). Verify with a manual trigger before relying on it.

## Testing

Module tests live **next to the module** they protect (`<module>/tests/test_*.py`); cross-cutting DSL-engine tests stay in the root `tests/`. Pattern: fabricate a `tmp_path` repo (scenarios + area data + reference crops), drive it through `DslScenarioTask.execute("bs1")` with a `_FakeActions` recording taps; assert recorded taps/matches. Fake frame sequences must model the UI after every tap (clear a red dot / button once clicked; include enough miss/hit frames for retries). Don't assert final `current_screen` in a pure fake-actions test unless the same updater runs.

**Always use `uv run pytest …`** (project convention).

## Common pitfalls

- **Hand-editing `area.yaml` / crops** — forbidden. Always via the Labeling UI (CLAUDE.md).
- **Bare `match:` for a "maybe-visible" element** — aborts the whole scenario on miss. Add a `steps:` block (or `while_match / max: 1`).
- **Forgetting `enabled`** — a disabled scenario silently never runs.
- **`ocr:` as a dict** — schema rejects it; `ocr:` is a scalar region name with sibling `store:`/`type:`/etc.
- **`store:` for data that must outlive the scenario** — it's `HDEL`'d at scenario start; use `state:` for durable facts.
- **Multiple action keys on one step** — rejected; split into separate steps.
- **Player-bound scenario with no `node:` and no `cond:`** — likely runs from any screen and breaks. Bind to a node or guard with `cond: currentNode == <screen>`.
- **Threshold too loose** — 0.85 false-positives on busy backgrounds; start at 0.9 and tighten.
- **`device_level: true` on something that needs the active player** — it won't have one; OCR `store:` writes to the wrong hash.
- **Expecting `screen_id` to scope duplicate region names** — it doesn't; rename the duplicate.

## Related

- **CLAUDE.md** (repo root) — authoritative for structure: module layout, the Labeling-UI rule, the SQLite/SQLCipher state DB, the Redis key layout, and the `uv` convention.
- Memory: [[enable-new-modules-by-default]], [[no-redundant-strict-false]], [[no-redundant-active-player-cond]].
