# ADR 0002 — Testing strategy: what earns a test here

Status: accepted · 2026-08-08

## Context

The suite reached ~5 000 tests at a 0.63 test:prod LOC ratio. An audit of both
trees found that a large share of it could not fail for any real defect. The
failure mode was consistent: **tests that restate configuration**. A scenario
YAML, an `area.yaml` region table, a `screen_verify.yaml` rule, a cost table in
`db/*.yaml` — each got transcribed into an `expected` literal in Python. Those
tests fail on every legitimate edit and catch nothing, and the churn data shows
the cost directly: the two most-edited test files in repo history were both pure
config mirrors (`tests/navigation/test_screen_verify_config.py`, 9 edits;
`games/wos/intel/tests/test_intel_module_skeleton.py`, 10).

Meanwhile the *valuable* tests here are unusually valuable, because the system
under test is a device the CI cannot touch. A pixel regression, a wrong OCR
threshold or a bad ranking decision is invisible until a bot misbehaves on a live
emulator, sometimes hours later.

So the question is not "how much coverage" but "which failures can only be caught
here".

## Decision

### The one rule

**A test must be able to fail for a reason that is not "someone edited a config
file on purpose".** If the only way to break it is an intentional data or YAML
change, it is a change detector — delete it or rewrite it into an invariant.

### What to test (in descending value)

1. **Real captured frames.** Detector tests over `references/*.png` — red dot,
   white border, tab active, hook detect, OCR landmarks. Keep the *named
   false-positive* cases especially: each one encodes a bug that shipped
   (`test_shop_fire_crystal_tab_icon_is_not_a_red_dot`). Nothing else can catch a
   threshold regression.
2. **Decisions and rankings.** Planner picks, `pop_due` ordering, allocator
   verdict traces, preempt arbitration. Pure logic with no other observer, and
   the thing `botctl why` reports to the operator.
3. **Protocol and concurrency.** The scrcpy control encoding, queue races against
   a real Redis. Unreproducible by hand; catastrophic when wrong.
4. **Repo-wide invariants.** One parameterized test that sweeps *everything*
   beats N per-module copies: every crop matches its bbox, every scenario's
   `exec:` name resolves, every verify rule is a recognised form. These scale for
   free as modules are added.
5. **Guards on a deliberate absence.** "`claimed` must not be OCR-detected",
   "the exit-confirm flow must never tap Confirm". A validator cannot know these;
   they encode a decision that is easy to undo by accident.

### What not to test

- **Config mirrors.** Do not restate a bbox, a region name, a rule dict, a cron
  string, a cost table or a scenario's step list in Python. Assert *invariants*
  instead: costs increase with level, ids are unique, every level declares a
  cost, no `${placeholder}` survives rendering.
- **Anything `startup_validation.py` already checks.** It runs on every boot and
  covers region references, `push_scenario` targets, cron task resolution, edge
  taps, dead-end and unreachable screens, red-dot capability, template files. A
  test duplicating one of those is redundant by construction. Extend the
  validator instead — it protects production, a test only protects CI.
- **Registration one-liners.** `assert "x" in registry` directly above a test
  that calls `registry["x"]` cannot fail independently.
- **Mock tautologies.** Configuring a mock and then asserting it was called, or
  asserting the value the mock was told to return.
- **Constant mirrors.** `assert TIMEOUT == 30`, `assert len(TOOLS) == 26`.
- **Snapshots of documents a human maintains.** `doc == snapshot` over a scenario
  YAML is a machine-updated copy of that YAML; `--snapshot-update` makes the
  failure meaningless. Snapshot only genuinely derived output.
- **Smoke tests.** `is not None`, `callable(...)`, "does not raise" with no
  assertion.

### Rules for writing them

- **Assert the outcome, not the traversal.** A fake-actions replay that only
  counts taps, or asserts `call("bs1", ANY)`, proves nothing. Assert the tap
  *coordinate* when the point is that it must miss a look-alike control, or the
  resulting state/decision otherwise.
- **Never assert log text.** Assert the structured payload the code produces. Log
  strings get reformatted for humans; that must not break CI.
- **Parameterize across siblings instead of copying.** Shared logic (the
  `core/ladder.py` family, the three points scorers, role tilt) gets one
  parameterized test at the shared layer plus per-domain tests for what is
  genuinely domain-specific.
- **A permanently skipped test is deleted, not parked.** `skip(reason="rewrite
  later")` never gets rewritten; git remembers it.
- **Tests for a disabled module.** Keep them next to the module — deleting the
  tests but keeping the code makes the module harder to revive. Delete them only
  together with the module.

### Where tests live

Unchanged: next to the module under `games/<game>/<id>/tests/`, cross-cutting in
root `tests/`, shared fixtures in the root `conftest.py`. Note that `conftest.py`
neutralizes the operator's `.env` slices (`WOS_INSTANCES`, `WOS_MODULES`,
`WOS_SCENARIOS`) at session start and per test — a local slice must never change
what CI runs.

## Consequences

Fewer tests, and the ones left have a higher hit rate. The cost is that some
classes of typo now surface at boot (via the validator) or on first run (via the
`exec:` failure contract) rather than in CI — which is the right trade, because
those two mechanisms protect the running fleet and a test only protects the
commit.
