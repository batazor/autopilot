---
name: dsl-scenarios
description: Author, edit, debug DSL scenarios (YAML files under scenarios/). Use when adding a new scenario, modifying an existing one, debugging why a scenario doesn't fire / loops / skips steps, or designing while_match / match / ocr / loop logic. Covers schema, step semantics, idioms, testing, and known gotchas. Trigger words — scenario, while_match, match, ocr, dsl step, area.json, regions.
---

# DSL scenarios — author & debug

YAML "programs" the bot executes inside the game. Each scenario describes a sequence of UI actions (taps, swipes, OCR reads, image probes) plus control flow (loops, conditionals).

## Files & layout

- `scenarios/<group>/<key>.yaml` — one scenario per file; filename (sans `.yaml`) is the scenario key.
- `modules/<id>/scenarios/` and `modules/core/<id>/scenarios/` — same rules as core; see skill **`wos-modules`** for manifests, overlay pages, and scope.
- `scenarios/drafts/**` — ignored by the resolver; safe scratch space.
- `scenarios/{placeholder}.yaml` — template form, resolved by `scenarios/template_resolver.py` (e.g. `level_up_{hero}.yaml` → `level_up_chenko`).
- Reference image crops: `references/crop/<screen>.<region>.png`. The `<screen>` prefix comes from the screen the region is registered on in `area.json`.
- Regions (bbox + per-region action metadata): `area.json`. Region names are globally unique; `screen_id` is for screen detection, not a namespace for DSL lookup. **NEVER edit `area.json` by hand — only via the annotator UI** (`uv run streamlit run ui/area_annotator.py`).
- Overlay rules: `modules/core/*/analyze/analyze.yaml` and `modules/*/analyze/analyze.yaml` (merged at runtime — not `analyze/analyze_pages/`).

## Schema (authoritative source)

- Pydantic: `scenarios/dsl_schema.py` (`DslScenario`, `DslStep`). Models use `extra="allow"`, so unknown keys round-trip but are not validated.
- Runtime executor: `tasks/dsl_scenario_execute_mixin.py` (top-level loop), `tasks/dsl_scenario_inline_mixin.py` (nested steps), `tasks/dsl_match_mixin.py` (match/while_match probes), `tasks/dsl_ocr_mixin.py` (ocr step), `tasks/dsl_scenario_helpers.py` (cond eval, parsers, helpers).

If runtime and schema disagree, the runtime wins. Keep both in sync when adding a step or modifier.

### Top-level scenario keys

```yaml
name: "Human-readable label"          # required
enabled: true                          # default false → scenario ignored at load
device_level: true                     # popup-style, no player binding (see below)
priority: 80_000                       # queue priority, default DEFAULT_SCENARIO_PRIORITY (80_000)
node: mail                             # node graph target to navigate to before running
cron: "0 */1 * * *"                    # cron schedule (player-bound scenarios only)
cond: "active_player != null"          # scenario-level guard, evaluated before steps
icon: "scenarios/icons/foo.png"        # optional UI icon for ops dashboard
steps: [...]                           # the actual program
```

**`device_level: true`** means the scenario is a generic UI dismissal (popups, reconnect prompts) — no player identity needed, retries default to 1, zero iterations of `while_match` is "ok, nothing to do." Player-bound scenarios (no marker) default to 5 × 500 ms initial-probe retries to absorb post-navigation lag.

### Step types

Each step carries **exactly one** action key (the validator enforces this), plus optional `cond` and modifiers. Action keys:

| Key                | Purpose                                                              |
|--------------------|----------------------------------------------------------------------|
| `click`            | Tap a region (uses bbox center; respects per-region `_search`)       |
| `long_click`       | Long-press                                                           |
| `match`            | Probe a region; fail aborts the whole scenario                       |
| `while_match`      | Probe, run `steps:` while matched, exit when not                     |
| `ocr`              | OCR the region; persist via `state:` or `store:`                     |
| `swipe_direction`  | Swipe `direction: up\|down\|left\|right`, `delta: <px>`              |
| `push_scenario`    | Enqueue another scenario (by key, or `{scenario, ...}` dict)         |
| `exec`             | Run an in-process action by name                                     |
| `wait`             | Sleep; accepts `0.5`, `"500ms"`, `"3s"`                              |
| `ttl`              | Early-exit, reschedule self for `now + ttl` (e.g. `"30m"`, `"2h"`)   |
| `repeat`           | Loop with `max:` / `until_match:` / `until_any_match:`               |
| `loop`             | Like repeat but the canonical name; supports `break: loop`           |
| `break`            | Exit nearest `loop:` / `repeat:`                                     |
| `system_back`      | Press Android system Back when set to `true`                         |

For conditional branching, use composite `cond:` blocks (see below) — there is no separate `if:` step.

**Action-less forms** (no action key) — both valid:

- **Composite `cond` block** — `cond:` + `steps:`. Runs the inner steps only when the condition holds.
- **Bare group** — only `steps:`. Inlines the inner steps (used with YAML anchors, see `read_mail_gifts.yaml`).

## Key idioms

**Guard pattern** — "run inner steps only if region is visible". Two forms:

```yaml
# Soft, guarded block (preferred for "maybe-visible" elements).
- match: tapanywhereyoexit
  threshold: 0.7
  steps:
    - click: tapanywhereyoexit
    - wait: 0.5s
  else:                          # optional
    - wait: 0.2s
```

```yaml
# Or, when you want loop-style behavior (run inner once if visible).
- while_match: button.claim
  max: 1
  steps:
    - click: button.claim
    - wait: 0.8s
```

**`match:` semantics depend on whether `steps:` (or `else:`) is present:**
- **Bare `match:`** (no `steps:`/`else:`) — hard gate. Miss aborts the scenario with `match_guard_failed`. Use for "this region MUST be present, otherwise abort."
- **`match: + steps:` / `+ else:`** — soft, guarded block. Matched → run `steps`. Miss → run `else` (if any) and continue to the next sibling step. Never aborts on miss. Use this when "maybe present, maybe not" is the normal state.

`while_match / max: 1` is functionally equivalent to `match + steps`; either form is fine. `while_match` makes sense when the inner `steps:` may also re-trigger the match (the loop will drain duplicates).

**`ttl:` step** — exits the scenario early and reschedules it for `now + ttl`. Use inside `else:` (or `while_match.else`) to back off when a popup never appears:
```yaml
- while_match: tapanywhereyoexit
  steps:
    - click: tapanywhereyoexit
  else:
    - ttl: 30m
```
Accepts `30m`, `2h`, `30s`, `500ms`, raw float seconds. Worker re-queues the same scenario at `now + ttl` via `_reschedule_if_needed`.

**Fallback with `else:`** — runs only when `while_match` had **zero iterations** (icon never seen):
```yaml
- while_match: button.claim
  max: 5
  steps:
    - click: button.claim
  else:
    - wait: 30s
    - push_scenario: some_recovery
```
When `else:` is provided on a player-bound `while_match`, it **bypasses** the strict-reschedule path — the scenario has declared its own no-match handling.

**Strict gate** — opt into "this step MUST have done work" on a player-bound scenario:
```yaml
- while_match: page.worker.add
  strict: true
  steps: [...]
```
Zero iterations after initial-probe retries → soft-fail + reschedule (`next_run_at = now + 30s`). Default is non-strict (steps are OR-semantics across the scenario). Don't add `strict: true` reflexively.

**Match-step modifiers** (work on both `match:` and `while_match:`):
- `threshold: 0.95` — template-match score; default `0.9`. Tighten for crowded screens.
- `min_match_saturation: 48` — reject low-saturation matches (kills grey-on-grey false positives).
- `isRedDot: true|false` — gate on the red-dot color check at the region.
- `isTabActive: true|false` — gate on tab-active visual marker.
- `isWhiteBorder: true|false` — gate on white-border visual marker.

**`ocr:` persistence** — two destinations, very different lifetimes:
- `state: path.to.field` → writes to **`db/state.yaml`**, the long-lived per-player state. Survives bot restarts; intended for "facts about the player" (level, alliance, etc.).
- `store: field_name` → writes to **Redis hash** `wos:player:<id>` (or `wos:instance:<id>:state` with `scope: instance`). **Ephemeral, scenario-step scoped** — gets `HDEL`'d at the start of every scenario run (see `_collect_ocr_store_targets`). Use for transient values consumed inside the same scenario.
- The legacy `to_state:` key is removed — don't use it.
- Both absent + a `region:` → defaults to `store: <region>`.

**Retry override** on `while_match`:
```yaml
- while_match: page.foo
  retry:
    attempts: 3
    interval: 250ms   # also accepts "0.5s" or raw seconds
```

## Cond expressions

Used both at scenario-level (`cond: ...`) and step-level. Evaluated by `_dsl_cond_allows_step` in `tasks/dsl_scenario_helpers.py`.

- `currentNode == main_city` / `currentNode != main_city` — node graph state
- `<field> == "value"` / `!=` — full-string, case-insensitive
- `<field> ~= "Upgrade|Build"` — case-insensitive substring; `|` is alternation
- `<field> == null` / `!= null` — "field empty/unset" — also accepts `nil`, `none`, `empty`
- LHS is a Redis hash field in `wos:instance:<id>:state` (e.g. `active_player`, `current_screen`).

## New scenario workflow

1. **Classify the scenario** — choose one primary shape:
   - overlay-triggered: an analyzer rule sees UI and pushes a scenario.
   - cron/player-bound: scheduled work for a player, usually with `node:`.
   - device-level popup: generic dismissal / onboarding / reconnect flow.
   - navigation/helper: a scenario that exists mostly to reach or verify a node.
2. **Choose the module and path** — write YAML under `modules/<id>/scenarios/` or `modules/core/<id>/scenarios/`; the filename without `.yaml` is the scenario key. Use module-local `analyze/analyze.yaml` for overlay rules.
3. **Decide the node contract** — use an existing `node:` when navigation already knows it. If the scenario needs a new node, do not rely on `node:` until navigation route/verify config exists.
4. **Identify regions** — open the annotator UI (`uv run streamlit run ui/area_annotator.py`) on a real screenshot, label new regions, and export crops. Never hand-edit `area.json`.
5. **Name regions explicitly** — follow the region naming rules below before writing YAML. Do not create duplicate short names and expect `screen_id` to disambiguate them.
6. **Add overlay trigger if needed** — in `modules/*/analyze/analyze.yaml` or `modules/core/*/analyze/analyze.yaml`, use `screens`, `region`, `action: findIcon|text|color_check`, optional `isRedDot`, `ttl`, and `pushScenario`. Analyzer YAML uses `findIcon`; `area.json` uses editor action `exist`.
7. **Stub the scenario YAML** — start with `enabled: false`, add `name`, optional `device_level`, `priority`, `node`, `cron`/`cond`, then `steps:`.
8. **Compose with guards** — use `match + steps` or `while_match + max: 1` for optional UI. Do not use bare `match:` for elements that may be absent.
9. **Rehearse live before broadening** — use AI Editor / MCP handles to capture screen, inspect current state, run or enqueue the scenario, then capture again after each click. Do not start `uv run wos` for scenario development/rehearsal; AI Editor owns this isolated flow.
10. **Add reference regression coverage** — save module-local screenshots/crops and write a focused test for the expected matches/clicks/node transitions.
11. **Validate**:
   - `uv run pytest tests/test_scenario_loader_declarative.py tests/test_startup_validation.py -q`
   - For new while_match work: `uv run pytest tests/test_dsl_while_match.py tests/test_dsl_while_match_strict.py -q`
   - For screenshot regression: run the new module/scenario test directly.
12. **Inspect AI Editor runtime state** — use Redis state from the AI Editor / IA queue executor flow:
   - `HGETALL wos:instance:<id>:state` — current node, last screen, last match outcome
   - `HGETALL wos:player:<player>` — `store:` fields
   - Queues / current scenario / history: see `MEMORY.md → reference_redis_cli`
13. **Flip `enabled: true`** only after the scenario behaves correctly with manual triggers.

## Live rehearsal workflow

Use this loop when developing against a real device. Scenario rehearsal runs through AI Editor, not the full `uv run wos` bot:

1. Capture the current screen and inspect `current_state` / `current_screen` before clicking.
2. Check whether the next DSL guard should match on the captured frame.
3. Perform the click via MCP/UI handle, not raw `adb`, so approvals, frame bus, and preview state stay in sync.
4. Capture again and verify the next frame before continuing.
5. Repeat until the scenario exits; then write a regression test from the screenshots.

AI Editor executes manual/UI-pushed scenarios through `src/ui/ia_queue_executor.py` without starting the full `uv run wos` bot. The embedded preview refresher must keep `current_screen` updated independently; if it is empty, a node-bound scenario can exit as `awaiting_screen_identity`. For step-level rehearsal, seed the correct node/screen only when the fake/live environment cannot update it itself.

## Region naming

- General reusable buttons: `button.<name>` (for example `button.claim`, `button.close`, `button.tap_anywhere_to_exit`).
- Region unique to one screen/page: `<pagename>.<name>` (for example `mail.title`, `alliance.main_city.new_alliance.mail.close`).
- Page title/header landmarks: `<pagename>.title`.
- Detector auxiliary regions:
  - primary detector: `<name>`
  - search window: `<name>_search`
  - tap target: `<name>_tap`
- Do not reuse a short name across unrelated screens when the element is page-specific. Prefer the page prefix so scenario YAML and overlay audit logs are unambiguous.
- For dismiss prompts, prefer the canonical reusable button name (`button.tap_anywhere_to_exit`) over older ad hoc names like `tapanywhereyoexit`.
- If a crop visually represents a button but OCR returns empty text, use `action: exist` and a button-style name instead of `action: text`.

## Module rename / scenario rename checklist

When moving an old core scenario into a clearer module name, update every contract together:

- Directory and `module.yaml` (`id`, `title`, `description`).
- Scenario filename, scenario key references, and `pushScenario` values in `analyze/analyze.yaml`.
- `routes/edge_taps.yaml` and `routes/screen_verify.yaml` region names.
- Region IDs in `area.json` via annotator/export, plus any reference crop names.
- Scenario tests, navigation tests, and screen-verify tests that assert landmarks or route taps.
- Redis/runtime cleanup if the old scenario key is still queued or marked active.

## Reference screenshots and regression tests

- Prefer module-local screenshots/crops in `modules/<id>/references/` when a module owns the scenario. Use the shared `references/` tree only when the module is already configured that way.
- Keep crops aligned with the annotator output. Reference image crops still follow the runtime convention `references/crop/<screen>.<region>.png` when using the shared tree.
- Every new non-trivial scenario should have a regression test that replays the relevant screenshot/crop state and asserts the behavior that matters:
  - overlay-triggered scenarios: expected overlay rule matches and `pushScenario`.
  - click/guard scenarios: expected tap region(s), no tap when optional match is absent.
  - node-bound scenarios: expected node guard/transition assumptions.
- Test pattern: fabricate a `tmp_path` repo (scenarios + area data + reference crops), run `DslScenarioTask.execute("bs1")` with fake actions, and assert recorded taps/matches. For overlay-only behavior, test the overlay rule against the saved screenshot/reference.
- Fake frame sequences must model the UI after every tap. If a red dot or button disappears after click, clear it in the next frame; if retries are expected, include enough miss/hit frames.
- Do not assert final `current_screen` in a pure fake-actions test unless the test explicitly runs the same updater that writes it.

## Scenario templates

**Overlay-triggered click scenario**

```yaml
# modules/<id>/analyze/analyze.yaml
overlay:
  - name: <module>.<page>.<thing>.visible
    region: <page>.<thing>
    action: findIcon
    device_level: true
    priority: 100_000
    threshold: 0.9
    screens: [<node>]
    ttl: 2s
    pushScenario:
      - name: <module>.click.<page>.<thing>
```

```yaml
# modules/<id>/scenarios/<module>.click.<page>.<thing>.yaml
name: Click <thing>
enabled: false
device_level: true
priority: 100_000
steps:
  - while_match: <page>.<thing>
    max: 1
    steps:
      - click: <page>.<thing>
      - wait: 0.5s
```

**Cron/node-bound scenario**

```yaml
name: Do <task>
enabled: false
node: <node>
cron: "0 */1 * * *"
cond: "active_player != null"
steps:
  - match: <page>.title
    threshold: 0.9
  - while_match: button.<action>
    max: 1
    strict: false
    steps:
      - click: button.<action>
      - wait: 1s
    else:
      - ttl: 30m
```

## Testing

Tests live in `tests/test_dsl_*.py`. Pattern: fabricate a `tmp_path` repo (scenarios + area.json + reference crops), drive it through `DslScenarioTask.execute("bs1")` with a `_FakeActions` recording taps. See `tests/test_dsl_while_match.py` for a tight reference.

**Always use `uv run pytest …`** (project convention; see memory `feedback_use_uv`).

## Common pitfalls

- **Adding regions by hand to `area.json`** — forbidden. Always via the annotator UI ([[feedback_area_json]]).
- **Letting `area.json` format churn leak into a scenario commit** — annotator/export can touch unrelated entries. Before committing, stage only the intended hunk and inspect `git diff --cached -- area.json`.
- **Bare `match:` for a "maybe-visible" element** — aborts the whole scenario on miss. Add a `steps:` block to switch to soft-guard semantics (or use `while_match: ... / max: 1`).
- **Forgetting `enabled: true`** — scenario silently never runs.
- **Expecting `screen_id` to scope duplicate region names** — DSL lookup is by global region name. Rename duplicates instead.
- **Using `store:` for data that must outlive the scenario** — it gets `HDEL`'d at scenario start. Use `state:` for persistent facts.
- **Multiple action keys on one step** — Pydantic validator rejects this; split into separate steps.
- **Player-bound scenario with no `node:` and no `cond:`** — likely runs from any screen and breaks. Either bind to a node or guard with `cond: currentNode == <screen>`.
- **Threshold too loose** — 0.85 commonly false-positives on busy backgrounds; start at 0.9, tighten.
- **`device_level: true` on something that needs the active player** — it won't have one; OCR `store:` will write to the wrong hash.
- **Assuming similarly named buttons are interchangeable** — verify the actual crop. For example, a mail `button.claim.big` template may not match a VIP page `button.claim`.

## Where to look when debugging

| Symptom                                           | First file                                              |
|---------------------------------------------------|---------------------------------------------------------|
| Scenario never picked up by loader                | `scenarios/loader.py`, `config/startup_validation.py`   |
| `match:` always fails                             | `tasks/dsl_match_mixin.py`, reference crop pixel-check  |
| `while_match` zero iterations / strict reschedule | `tasks/dsl_scenario_execute_mixin.py` (search `strict`) |
| `ocr` value not persisting                        | `tasks/dsl_ocr_mixin.py`, `config/state_store.py`       |
| Cond expression not gating as expected            | `tasks/dsl_scenario_helpers.py` (`_eval_*_cond`)        |
| Node navigation loop                              | node graph in `analysis/overlay_engine.py`              |
| "Where did this task come from / why did it run?" | **Timeline** — see below                                |

## Debug timeline

For correlating events end-to-end on a single task, use the **Debug → Timeline** Streamlit page (`/timeline`). It reads the bounded LIST at `wos:debug:timeline:<instance_id>` (cap 5000 / TTL 1h) populated by producers across the codebase:

| Event                       | Producer                                                |
|-----------------------------|---------------------------------------------------------|
| `overlay.matched`           | `worker/instance_worker_overlay.py`                     |
| `overlay.throttled`         | same — both push-ttl skip and `type: time` paths        |
| `queue.enqueued`            | `scheduler/queue.py:schedule()` (success)               |
| `queue.duplicate_skipped`   | `scheduler/queue.py:schedule()` (dedup hit)             |
| `queue.popped`              | `scheduler/queue.py:pop_due()` (winner)                 |
| `task.started`              | `worker/instance_worker_tasks.py`                       |
| `task.finished`             | terminal (success or non-preempt non-error finish)      |
| `task.failed`               | terminal (exception)                                    |
| `task.preempted`            | terminal (`metadata.reason == "preempted_by_…"`)        |
| `approval.requested`        | `actions/tap.py:_require_approval`                      |
| `dsl.step`                  | `tasks/dsl_scenario_execute_mixin.py` (top-level only)  |

Filter the page by `task_id` to see one task's full chain. CLI inspection:
```
redis-cli LRANGE wos:debug:timeline:bs1 0 50
```

The schema and helpers live in `debug/timeline.py`. Adding a new event type: extend `EVENT_TYPES`, update the table above. Producers that pass an unknown event are silently dropped (the whitelist is the contract).

## Related skills

- **`wos-modules`** — create or change `modules/core/*` / feature modules, `module.yaml`, overlay manifests, exec/UI/wiki wiring

## Related memory

- [[feedback_area_json]] — never edit `area.json` directly
- [[feedback_dsl_state_vs_store]] — `state:` vs `store:` lifetime rules
- [[feedback_use_uv]] — always `uv run`
- [[reference_redis_cli]] — Redis key layout for inspection
