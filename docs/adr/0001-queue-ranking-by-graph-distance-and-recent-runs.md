# ADR 0001: Queue ranking by graph distance and recent runs

- **Status:** Proposed  
- **Date:** 2026-05-11  

## Context

The worker schedules DSL scenarios through a Redis-backed priority queue (`scheduler/queue.py`). Today, due tasks are ordered primarily by static `priority` and `run_at`. That is simple but:

1. **Screen distance:** A scenario whose cron spec requires `node: X` may be enqueued while the UI is on another screen. Preferring work that is **fewer navigation hops** away from `current_screen` (from instance state, populated by probes such as `where_i_am`) should reduce wasted attempts and match-guard failures.

2. **Hot / broken scenarios:** A task type that is repeatedly started but rarely helps (fails, times out, or hits guards) can still dominate ordering if its base `priority` is high. We want **recently attempted** types to yield so other scenarios can run.

3. **Unreachable targets (node-bound only):** If a scenario has a **non-empty** required `node` and the static graph has **no path** from `current_screen` to that node, the navigator cannot fix that from the queue alone; such jobs should be **deprioritised** relative to reachable work. Scenarios **without** a required node are **not** “unreachable” for ranking — they stay neutral (see [Ranking model](#ranking-model)).

Related code today:

- Queue pop / sort: `RedisQueue.pop_due` sorts by `(-priority, run_at)` (see `scheduler/queue.py`).
- Graph distance: `navigation/screen_graph.py` (`bfs_route`, `route_hops` / `route_hops_async`).
- Cron `node` metadata is already scanned for gating (`_task_types_requiring_node` in `scheduler/queue.py`).

## Decision

### Invariant: `run_at` and due-ness (must not regress)

**Dynamic ranking applies only among tasks that are already due** — the same population `pop_due` already considers today (e.g. ZSET members with score `≤ now`). It **must never** promote a not-yet-due task or otherwise cause a task to run **before** its scheduled `run_at` / cron–TTL semantics.

Implementations must **not** re-score the entire queue or merge “would be nice next” items into the candidate set: that risks bypassing delayed `run_at`, dedup, or other enqueue contracts. **Only** reorder the subset that has already passed the time gate; use `run_at` only as a **tie-breaker** within that subset (as today), not as something dynamic ranking overrides.

---

Introduce an **effective ranking key** at **pop** time (not by rewriting every queued item on each screen change):

1. **Graph hop debuff**  
   - Resolve `task_type → required_node` from cron YAML (same source as node gating), cached in process. **Do not** conflate “`required_node` absent” with “unreachable” — see [Ranking model](#ranking-model) (node-independent vs unreachable).  
   - If `required_node` is **non-empty** and a graph path exists from `current_screen`: let `hops = len(bfs_path) - 1` and add **bounded** `graph_debuff` from `w_hops * f(hops)` (monotone `f`).  
   - If `required_node` is **non-empty** and there is **no path**: **unreachable** only — `unreachable_flag = 1`, sentinel `hops` in the sort tuple, **bounded** `graph_debuff` per policy (do not cross priority bands unless explicitly configured).  
   - If `required_node` is **absent / empty** (**node-independent**): no hop penalty — `graph_debuff` from graph distance is **0**, `unreachable_flag = 0`, `hops = 0` (device-level / housekeeping must not lose to false “unreachable”).

2. **Recent-run debuff (time-windowed)**  
   - **Policy (fixed):** `recent_key = (task_type, player_id)` as stored in the queue item (after any worker-side player resolution). **Rationale:** one account can be stuck in a broken run of scenario `S` while another account on the **same device** still needs `S`; debuffing by `task_type` alone would unfairly sink `S` for everyone. Per-player keys isolate hot paths per gamer.  
   - **Device-level tasks** use `player_id == ""`; their `recent_key` is `(task_type, "")` — all device-level executions of that type share one window (desired for e.g. repeated `where_i_am` / overlay dismissals on one device).  
   - **Storage: Redis `ZSET`** per instance at `wos:instance:<id>:recent_runs`.  
     - **Score** = wall-clock unix timestamp of the execution-start event.  
     - **Member** = `"<task_type>|<player_id>|<uuid4_hex_8>"`. The UUID suffix lets duplicate `recent_key`s coexist as distinct ZSET members (ZSET requires unique members; we want one entry per execution event, not deduplication).  
   - **On execution start** (task dequeued, `execute()` begins — **independent of success/failure**, so broken scenarios still accumulate history): pipeline three commands:  
     - `ZADD <key> <now> "<recent_key>|<uuid>"`  
     - `ZREMRANGEBYSCORE <key> -inf (now - window_seconds)` — prune entries older than the window so storage stays bounded.  
     - `EXPIRE <key> (2 * window_seconds)` — a dead worker won't leave permanent garbage.  
   - **At ranking time** (inside `pop_due`): one `ZRANGEBYSCORE <key> (now - window_seconds) +inf` returns all recent events; group by `recent_key` prefix to compute `count_in_window` per candidate. The pruning-on-append keeps the returned set small.  
   - **`window_seconds`** is configurable under worker settings (default **1800s = 30 min**). Wall-clock windowing is stable regardless of task-rate fluctuations — an idle device after a busy session won't carry over yesterday's history.  
   - **Debuff curve** (linear capped): `recent_debuff = min(count_in_window, recent_cap) * w_recent`. Default `recent_cap = 3` so a task spinning 10× still only pays at most `3 * w_recent`. A broken task that keeps re-entering the window therefore **sinks further** than a healthy one, but the penalty **stops growing** after the cap so one pathological type cannot dominate the debuff magnitude forever.

3. **`current_screen` unknown / empty**  
   Treat **unknown** and **empty** `current_screen` the same for policy below (instance state not yet usable for graph checks).

   - **Node-independent** tasks (`required_node` absent / empty per cron) **may run normally** among due work: `graph_debuff = 0`, `unreachable_flag = 0`, `hops = 0`. Ranking must not invent a hop penalty because the screen is unknown.  
   - **Node-gated** tasks (cron declares a required `node` / task type is in the “requires known screen” set used by `pop_due`) **must not be popped** unless the **existing** `RedisQueue.pop_due` eligibility rules already allow them — i.e. do **not** widen the candidate set when the screen is unknown. **Ranking must never substitute for that gate:** we must not run “needs `main_city`” work while the UI location is still unknown.  
   - **Identity / probe** tasks that exist to **establish** state (e.g. `where_i_am`, and other `device_level: true` probes) **remain eligible** when the screen is unknown and **must not receive graph debuff** (`graph_debuff = 0`, neutral `hops` / `unreachable_flag`), so the worker can still run `detect_screen` / OCR bootstrap before node-bound cron work re-enters the race.

   This explicitly prevents: *“we do not know where we are, but we still schedule a scenario that assumes a specific screen.”*

4. **Ordering (among due tasks only)**  
   - After the existing **due** filter (`run_at ≤ now`, instance/player gates unchanged), sort **only** that list using the tuple in [Ranking model](#ranking-model) (not the global ZSET). A task with an earlier `run_at` may still run after another **due** task if the latter’s **effective** rank wins — that is fine. The invariant above is **not** “`run_at` wins over everything”; it is **never run before `run_at`**, and **never pull not-due work into the candidate set**.

5. **Cooperative preemption (between DSL steps)**  
   Static priority alone cannot interrupt a task that has already started. A long-running scenario (e.g. `where_i_am` doing OCR fan-out, `building.upgrade` waiting for confirmations) blocks the worker even when higher-priority work — like a banner-dismiss `pushScenario` enqueued by a rolling overlay tick — is sitting **due** in the queue.

   - **Where it runs:** in `tasks/dsl_scenario.py`'s main `while step_index < len(steps)` loop, **before each step**. The existing `_preempted_by_new_debug(instance_id)` hook is the precedent; add `_preempted_by_higher_priority(instance_id, running_effective_priority)` next to it. Not inside `while_match` / `until` inner iterations — those are bounded by their own timeouts and the next outer step boundary is reachable quickly.  
   - **Peek API:** add `RedisQueue.peek_top_due(instance_id, current_screen) -> QueueItem | None` returning the top **due** candidate's full item **without popping**, using the same ranking tuple as `pop_due`. Cheap: top-of-ZSET + the same rank computation.  
   - **Threshold:** yield only when `top_pending.effective_priority - running.effective_priority >= PREEMPT_MARGIN` (default **5_000**, configurable). Prevents thrashing between two ~equal tasks (an 80k+hops=2 yielding to 80k+hops=1 is net negative — extra Redis ops, re-enqueue cost, possibly re-running `_navigate_to_node`).  
   - **Yield path:** when preemption fires, `DslScenarioTask.execute` returns  
     ```python
     TaskResult(
         success=False,
         next_run_at=datetime.now(),
         metadata={
             "reason": "preempted_by_higher_priority",
             "preempted_by": top_pending.task_type,
             "preempted_by_priority": top_pending.effective_priority,
             "yielded_at_step": step_index,
         },
     )
     ```
     The yielded task is immediately due again on the next `pop_due` and will compete with whatever has been added / finished in the meantime.  
   - **No double-debuff on yield:** the yielded task's `recent_key` was **already appended at execution start**. Do **not** add a second append on yield — otherwise a single yield would double-count as two attempts and unfairly debuff the yielded task.  
   - **Step resume:** keep existing `start_step_index` semantics (`worker/instance_worker_tasks.py:_resume_step`). When the scenario has a root `node:` (route-required), yield resets to step 0 to redo navigation; otherwise resume from the yielded step.  
   - **Anti-starvation (v1):** per-`task_id` `yield_count` on the in-memory task object. After **3 yields** in one dequeue cycle, the task becomes immune to preemption for the rest of this run (continues to completion). Logged so support can spot pathological churn.

## Ranking model

Dynamic ranking applies **only** to tasks that are already **due** (same filter as today: score `≤ now`, instance/player gates unchanged). Nothing in this section changes **when** a task becomes eligible — only **which due task** is popped first.

### When `current_screen` is unknown / empty

After `pop_due`’s **time + instance + player + node gates**, the **due** list for ranking should contain only tasks that policy allows while the screen is unknown (Decision §3): **node-independent** work, **device-level** probes (`where_i_am`, …), and any other types explicitly allowed by the existing gate — **not** node-gated routine scenarios that require a known `current_screen`.

For every such **allowed** candidate while `current_screen` is unknown:

- Apply **no graph distance debuff**: `graph_debuff = 0` from the screen graph (same neutral tuple as **Node-independent** in the classification below).
- Do **not** classify as **Unreachable** using the graph (path checks require a known start node); node-bound tasks that need a path should already be absent from candidates.

Once `current_screen` is known, resume **Reachable** / **Unreachable** classification for non-empty `required_node` as in the next bullets.

For each **due** candidate:

- `base_priority` — static priority from the queue payload (YAML / overlay / seed at enqueue time).
- `required_node` — resolved from cron metadata for that `task_type` (**empty / absent** = scenario is **not** node-bound for ranking).

**Node-independent** (`required_node` absent / empty after normalisation):

- `graph_debuff = 0` (no hop-distance penalty — these tasks are not “trying to reach” a declared screen).
- `unreachable_flag = 0`.
- `hops = 0` in the sort tuple (neutral; they do not compete on graph distance).

This avoids mis-classifying device-level / housekeeping / overlay-pushed scenarios as unreachable and unfairly demoting them.

**Unreachable** (**only** when `current_screen` is **known**, `required_node` is **non-empty**, and there is **no** graph path from `current_screen` to `required_node`):

- `unreachable_flag = 1`; **`hops`** in the sort tuple uses a **sentinel** larger than any real path (e.g. `10**9`).
- Apply **bounded** `graph_debuff` per policy; the candidate must sort **after** any candidate with `unreachable_flag == 0` that ties on `effective_priority`, so unreachable work does not starve reachable work at the same effective rank.

**Reachable, node-bound** (`current_screen` **known**, `required_node` non-empty, **and** a path exists):

- `unreachable_flag = 0`.
- `hops` = shortest-path hop count (finite), used for tie-breaking and for computing the hop-based part of `graph_debuff`.

- **Recent-run history** — **persisted in Redis per `instance_id`** (see Decision §2): sliding window of `recent_key` values used to compute `recent_count` / `recent_debuff`.

Define:

`effective_priority = base_priority - graph_debuff - recent_debuff`

where `graph_debuff` and `recent_debuff` are **non-negative** and **bounded** by policy so a single dynamic adjustment does not, by default, punch a task through an entire configured priority band; **crossing** intentional bands (e.g. tutorial vs routine) requires an **explicit** configuration decision, not an accidental side-effect of debuff magnitude. The cases **Node-independent** / **Unreachable** / **Reachable, node-bound** above fully define `unreachable_flag` and the tuple field **`hops`** used below.

**Final sort key** (Python `sort` / `sorted` ascending — **smaller tuple runs first**):

```text
(-effective_priority, unreachable_flag, hops, run_at, created_at)
```

- **First key:** higher `effective_priority` wins (more negative `-effective_priority` sorts first).
- **Then:** reachable (`unreachable_flag == 0`) before unreachable (`1`).
- **Then:** fewer `hops` first (`0` for node-independent, finite path length for reachable node-bound, sentinel only for unreachable — already ordered after by `unreachable_flag`).
- **Then:** earlier `run_at` (stable cron / delay semantics within the due set).
- **Then:** `created_at` — **stable last tie-breaker**. **Add `created_at: float` to `QueueItem`** and set it to `time.time()` inside `RedisQueue.schedule()`. Lexical `task_id` is **not** an acceptable surrogate because current task IDs contain random UUID suffixes (`ovl:bs1:<scenario>:<uuid8>`, `startup:bs1:<scenario>:<uuid8>`) — sorting by them is effectively random and would defeat stable tie-breaking. `enqueue_seq` (Redis `INCR`) was considered but rejected: extra Redis round-trip per enqueue, and `time.time()` is monotonic enough at our queue volume (sub-millisecond resolution suffices).

## Test cases (golden fixtures)

Minimal **ordering** scenarios for unit / property tests — build small synthetic **due** sets (same `instance_id`, known `current_screen` unless noted), stub graph + Redis recent window, assert **pop order** or **sort key** order. These are not exhaustive but catch the main regressions.

1. **Same `base_priority`, same `run_at`, both reachable node-bound:** the task with **fewer `hops`** wins (tie-break on the third tuple component after `-effective` and `unreachable_flag` match).

2. **Same `base_priority`, same finite `hops`, same `unreachable_flag`:** the task with **lower `recent_count`** in the Redis window wins (lower `recent_debuff` → higher `effective_priority`; if `effective` ties, identical hop tie-break — then **`run_at`** / **`created_at`** surrogate decides; fixture should hold `run_at` equal and set distinct surrogate so **recent** is the only differentiator).

3. **Same `effective_priority`, one reachable and one unreachable** (`unreachable_flag` 0 vs 1): **reachable wins** — second sort key favours `0` before `1`.

4. **Priority band guard:** with default bounded debuffs, a task in a **lower YAML band** must **not** outrank a task in a **higher** band solely because of **graph_debuff** (unless configuration explicitly allows stronger penalties / band crossing). Golden: pair at `base_priority` 80_000 vs 70_000 — higher base wins regardless of hop debuff on the lower item.

5. **No `required_node` (node-independent):** treated as **reachable** for ranking, **`graph_debuff = 0`**, `hops = 0`; must **not** be classified unreachable when `current_screen` is unknown or known. Compare against a node-bound peer so the independent task is not spuriously demoted.

6. **Due-ness invariant:** a **not-due** task (future `run_at`, or absent from the due candidate set) **never** appears as the winner **regardless** of what its hypothetical `effective_priority` would be — dynamic ranking runs **only** on the filtered due list (Decision: **Invariant: `run_at` and due-ness**).

Optional **7. Unknown `current_screen`:** node-gated cron tasks are **not** in the due list (existing `pop_due` gate); fixture asserts only **node-independent** / **device-level** probes remain and receive **zero** graph debuff from the graph layer.

**Cooperative preemption fixtures:**

8. **Preemption margin holds:** running task at `effective_priority=80_000`, peek sees pending at `effective_priority=83_000`. Default margin `5_000` → **no yield** (gap 3_000 < 5_000). Re-run with margin `0` → **yield**. Sanity check that the threshold is honoured.

9. **Banner preemption (the real-world case):** running `where_i_am` at `effective_priority=83_000`, overlay tick enqueues `tap_confirm_button` at `effective_priority=88_000`. Default margin `5_000` → 88_000 − 83_000 = 5_000 ≥ margin → **yield**. Yielded task re-enqueues at `next_run_at=now`; on the next `pop_due` it is again a candidate.

10. **Anti-starvation kicks in:** force three sequential yields for the same `task_id` (each yield re-enqueues, then the next `pop_due` runs it, then it yields again). On the **fourth** in-step check, the running task is immune (`yield_count >= 3`) and **does not yield**, even though a higher-priority pending task is waiting. Logged event for the immunity.

## Observability

Dynamic ranking answers *“why did Y run before X?”* — without telemetry that becomes guesswork (“why doesn’t the bot do X?”). Ship **logs**, **metrics**, **operator/debug visibility**, and **approval UI** together with the ranking logic (or immediately after).

### Structured logs (on each successful `pop_due` / task selection)

Emit one structured record for the **chosen** task (and optionally, at `DEBUG`, one line per **due** candidate with the same field set) so support can diff two pops without re-running the worker.

**Minimum field set** (names are suggestions; keep stable once shipped):

| Field | Meaning |
|--------|--------|
| `task_type` | Queue `task_type` / scenario key |
| `player_id` | Queue player id (`""` for device-level) |
| `base_priority` | Static priority from queue payload |
| `effective_priority` | `base_priority - graph_debuff - recent_debuff` (or equivalent single number used for sort) |
| `current_screen` | Instance `current_screen` at pop time |
| `required_node` | Resolved cron `node` for this `task_type`, if any |
| `hops` | `0` if node-independent; finite hop count if reachable node-bound; sentinel if unreachable |
| `reachable` | `false` **only** when `current_screen` is known, `required_node` is non-empty, and there is no graph path; `true` if node-independent, screen unknown (neutral), or path exists |
| `recent_key` | Optional string/tuple serialisation of `(task_type, player_id)` for logs |
| `recent_count` | `count_in_window` for that candidate’s `recent_key` |
| `graph_debuff` | Numeric contribution from graph policy |
| `recent_debuff` | Numeric contribution from recent-window policy |
| `run_at` | Scheduled run time from queue item |
| `created_at` | `QueueItem.created_at` — final tie-break value |

Add `instance_id`, `task_id` for correlation with existing worker logs.

**Preemption events** emit a **separate** structured line at the moment of yield (not on the next pop):

| Field | Meaning |
|--------|--------|
| `event` | `"preempted"` (literal) |
| `task_type` | Running task that yielded |
| `running_effective_priority` | The yielder's `effective_priority` |
| `preempted_by` | `task_type` of the pending task that triggered yield |
| `preempted_by_priority` | Pending task's `effective_priority` |
| `yielded_at_step` | `step_index` reached before yielding |
| `yield_count` | Cumulative yields for this `task_id` (1 on first, 2 on second, …) |
| `immune` | `true` when `yield_count >= 3` and the running task **refused** to yield (anti-starvation triggered) |

### Metrics

Expose counters (or labelled histograms) so regressions show up in dashboards, not only in grep:

- Pops where **`reachable == false`** (known `current_screen`, non-empty `required_node`, no graph path — **not** “screen unknown” neutrality).
- Pops where **`graph_debuff > 0`** / **`recent_debuff > 0`** (optionally break down by band: e.g. hops 1 vs 2+).
- **Preemption yields**: counter incremented per yield (labels: `task_type` yielder, `task_type` preemptor). Sudden spike means a hot loop of mutual yielding — investigate margin / scenario churn.
- **Anti-starvation immunity**: counter incremented when `yield_count >= 3` immunity kicked in. Should be near-zero in healthy operation.
- Optional: **Redis recent-history** read/write failures or “degraded ranking” events (if the implementation falls back when Redis is down).
- Optional: count of **`pop_due` candidate set size** to spot queue storms.

### Debug / operator tools

- A **debug flag or one-off command** (CLI, Redis flag, or Streamlit toggle) that prints or returns **“why this task won”**: sorted top‑N due candidates with `effective_priority`, debuff breakdown, and `run_at` — enough to reproduce the comparison by hand.
- Reuse the same breakdown in **automated tests** — implement the cases in [Test cases (golden fixtures)](#test-cases-golden-fixtures) as **golden ordering fixtures** so ranking changes stay intentional.

### Approval UI (Streamlit / `uv run wos`) — **deferred to v2**

The project already has an **approval UI** for taps / risky actions. A future iteration may add a **panel or expandable block** on the active / next task view showing the same **“why this task”** summary: `task_type`, `player_id`, `recent_key`, `base_priority` → `effective_priority`, `current_screen`, `required_node`, `hops`, `reachable`, `recent_count`, `graph_debuff`, `recent_debuff`, `run_at`, `created_at`. Operators waiting on approvals would see **why the scheduler picked this item** without opening logs.

**Deferred from v1** to keep the first release reviewable — the structured logs + debug command cover the same need for support; approval-UI integration is purely operator ergonomics on top.

---

## Consequences

### Positive

- Shorter navigation paths are preferred without hand-tuning every scenario pair.
- Flaky or over-scheduled task types naturally fall behind healthier ones.
- **Node-bound** targets with **no graph path** no longer starve reachable / node-independent jobs at the same base priority; node-independent work is not mis-labelled unreachable.
- **Unknown `current_screen`:** node-independent and identity probes can still run; node-gated “needs this screen” work stays behind the existing `pop_due` gate; ranking does not bypass that or apply bogus graph debuffs to probes (`where_i_am`, …).
- **Recent-run debuff** uses Redis-backed **time-windowed ZSET** history per instance, so fairness survives **worker restarts** and stays consistent if multiple consumers read the same instance state. Wall-clock windowing makes the policy stable regardless of task-rate fluctuations.
- **Cooperative preemption** addresses the real-world failure mode where a slow scenario (e.g. `where_i_am` OCR fan-out, building-upgrade waits) blocks higher-priority work indefinitely — banner-dismiss `pushScenario` items can finally interrupt at step boundaries.
- **Stable tie-breaking:** `QueueItem.created_at` removes the random-UUID ordering surprise where two items with identical priority/hops/run_at sorted unpredictably.
- **Explainability:** structured logs + metrics + debug mode (see [Observability](#observability)) make *“why doesn’t the bot do X?”* answerable from evidence, not from reading `scheduler/queue.py` alone.

### Negative / risks

- **Log volume:** logging every due candidate at `DEBUG` can be noisy on busy instances; default to **one structured line for the winner** at `INFO`, candidates only when debug is on. Preemption events are sparse, log unconditionally at `INFO`.
- Extra CPU on each `pop_due`: BFS + cache lookups + ZSET range read — negligible vs ADB/OCR if cached.
- Wrong or stale graph edges will mis-rank until YAML/graph is fixed (same class of bug as navigation today).
- **Starvation of “far but important”** work is possible if hop debuff is too aggressive; mitigated by bounded debuff, floor on effective priority, or periodic base-priority bands — tune in implementation.
- **`recent_key` must stay aligned with queue resolution** — if the worker rewrites `player_id` when dequeuing, history and candidate debuff must use the **same** resolved id, or counts will skew.
- **Redis read/write cost** — each `pop_due` reads one ZRANGEBYSCORE; each execution start writes one ZADD + one ZREMRANGEBYSCORE + one EXPIRE (pipelined). Bounded by `window_seconds`; failures degrade to `recent_debuff = 0` with an explicit metric so we can see if ranking is running degraded.
- **Preemption thrashing:** two ~equal-priority tasks could ping-pong if margin is too low. Default `PREEMPT_MARGIN = 5_000` plus the `yield_count >= 3` immunity guards against this; metric on yields catches regressions.
- **Re-enqueue cost on preemption:** yielded task must re-run any setup steps (e.g. root `node:` navigation). For node-scenarios, yield resets `start_step_index` to 0, so navigation re-runs on the next pop. Acceptable trade-off for unblocking the queue.

## Non-goals (for this ADR)

- Replacing overlay `runScenario` vs `pushScenario` policy (see `worker/instance_worker_overlay.py`).
- Changing static YAML `priority` bands for tutorials/ads — dynamic terms should fit **within** those bands.

## v1 implementation scope

The first release ships **all of the following together** (review can split into separate PRs, but the **behaviour** is a single landing):

1. **`QueueItem.created_at: float`** added at `RedisQueue.schedule()` time; used as last tie-breaker. Backfill: missing on legacy items → treat as `0.0` (sorts first, harmless for stale stragglers).
2. **Graph debuff** with bounded `w_hops`, BFS cached in process keyed by `(current_screen, required_node)`; cache invalidated only on `screen_graph` reload.
3. **Recent-run debuff** via Redis ZSET `wos:instance:<id>:recent_runs`, time-windowed (default `window_seconds=1800`), linear-capped curve, `recent_cap=3`.
4. **Cooperative preemption** between DSL steps, `PREEMPT_MARGIN=5_000`, `yield_count>=3` immunity, structured yield events.
5. **Structured logs** for winner + preemption events at `INFO`; candidate logs at `DEBUG`. Metrics for `reachable=false`, yields, immunity, optional ZSET failures.
6. **Golden ordering fixtures** (test cases §1–10) as `pytest` integration tests.
7. **Debug command** to dump top‑N due candidates with effective-priority breakdown.

**Deferred to v2** (or later, by separate ADR if needed):

- Approval-UI panel (operator ergonomics, structured logs are sufficient for support).
- Tunable `w_hops` / `w_recent` / `PREEMPT_MARGIN` via runtime UI (start with code-level defaults).
- Cross-instance global ranking (currently per-instance only).

## Status note

**Proposed** — behaviour described here is not implemented yet; flip status to **Accepted** when v1 scope is agreed and an implementation branch starts. Future deviations from this ADR during implementation should land as an amendment commit on this file (or, if the deviation is large, a new ADR that supersedes this one).
