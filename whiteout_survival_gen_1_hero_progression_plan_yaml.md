# Whiteout Survival — OR-Tools-first Hero Upgrade Optimizer

Цель: сразу строить планировщик на OR-Tools CP-SAT. Greedy можно оставить только как fallback/debug, но не как первый production backend.

---

## Архитектура

```text
runtime_state.yaml
    ↓
command_catalog.yaml
    ↓
candidate_generator
    ↓
score_model
    ↓
ortools_cp_sat_optimizer
    ↓
selected semantic commands
    ↓
executor
```

OR-Tools не знает, какие герои хорошие. Поэтому scorer обязателен: он переводит доменную пользу upgrade-команды в integer value для objective.

---

## Основная идея модели

Каждый возможный upgrade — бинарная переменная:

```text
x_command ∈ {0, 1}
```

Objective:

```text
maximize Σ score(command) * x_command
```

Constraints:

```text
Σ cost_hero_xp(command) * x_command <= available_hero_xp
Σ cost_skill_books(command) * x_command <= available_skill_books
Σ cost_shards(command) * x_command <= available_shards
Σ cost_widgets(command) * x_command <= available_widgets
Σ cost_gems(command) * x_command <= spendable_gems
```

Hard rules:

```text
x_command = 0 if blocked_by_rule(command)
x_next_step <= x_previous_step for chained upgrades
```

---

## First implementation slice

Нужно реализовать только один solver path:

```yaml
optimizer:
  type: ortools_cp_sat
  time_limit_seconds: 0.25
  num_search_workers: 4
  objective: maximize_total_value
  execute_policy:
    mode: top_k
    k: 1
    reoptimize_after_each_command: true
```

Сначала выполняем только `top 1` команду после каждого solve. Это безопаснее: после каждого upgrade меняются ресурсы и marginal ROI.

---

## Candidate model

```yaml
candidate:
  id: jessie_expedition_skill_1_4_to_5
  action: skill_up
  hero: jessie
  target:
    track: expedition
    slot: 1
    from: 4
    to: 5
  costs:
    epic_expedition_manual: 10
  score:
    base: 4200
    threshold_bonus: 3000
    replacement_penalty: 0
    resource_penalty: 800
    final: 6400
  constraints:
    - hero_unlocked
    - skill_below_max
    - resource_available
    - bear_joiner_priority
```

---

## Minimal runtime state

```yaml
account:
  server_age_days: 7
  furnace_level: 13
  current_generation: 1
  drill_camp_unlocked: true
  hero_gear_unlocked: false
  objective_profile: f2p_bear_and_growth

resources:
  hero_xp: 120000
  gems: 18000
  shards:
    mythic_general: 10
    epic_general: 35
    molly: 8
    sergey: 20
    bahiti: 14
    jessie: 40
    jasser: 12
  manuals:
    expedition:
      rare: 20
      epic: 14
      mythic: 3
    exploration:
      rare: 18
      epic: 10
      mythic: 2

heroes:
  molly:
    unlocked: true
    level: 31
    star_level: 2
    star_tier: 1
    skills:
      exploration:
        1: 3
        2: 2
      expedition:
        1: 2
        2: 1

  sergey:
    unlocked: true
    level: 30
    star_level: 2
    star_tier: 0
    skills:
      expedition:
        1: 2
        2: 2

  jessie:
    unlocked: true
    level: 10
    star_level: 2
    star_tier: 0
    skills:
      expedition:
        1: 4
        2: 1
```

---

## Hard constraints

```yaml
hard_constraints:
  - id: resource_capacity
    type: linear_capacity
    resources:
      - hero_xp
      - gems
      - mythic_general_shards
      - epic_general_shards
      - expedition_manuals
      - exploration_manuals
      - gear_xp
      - widgets

  - id: deny_mythic_general_shards_gen1_default
    type: deny_resource_usage
    resource: mythic_general_shards
    deny_if:
      hero_generation: 1
      hero_not_in:
        - molly

  - id: stop_sergey_deep_after_flint
    type: deny_actions
    hero: sergey
    actions:
      - star_tier_up
      - gear_enhance
      - exclusive_gear_up
    when:
      hero_unlocked: flint

  - id: support_level_cap
    type: cap_level
    heroes:
      - jessie
      - jasser
      - patrick
      - seo_yoon
    max_level_before_drill: 40
    max_manual_level_after_drill: 0

  - id: skill_cap_by_star
    type: legal_gate
    action: skill_up
    rule: target_skill_level_must_be_allowed_by_current_star_level

  - id: reserve_gems_for_wheel
    type: spendable_capacity
    resource: gems
    reserve_floor: 13500
```

---

## Scoring config

```yaml
scoring:
  output_scale: int_0_10000

  profile_weights:
    f2p_bear_and_growth:
      expedition: 30
      exploration: 25
      arena: 15
      bear_join: 30
      economy: 0

  action_sunkness:
    level_up_pre_drill: 35
    level_up_post_drill: 15
    skill_up: 60
    star_tier_specific_shards: 80
    star_tier_general_shards: 100
    gear_enhance: 55
    exclusive_widgets: 100
    gems: 95

  threshold_bonus:
    bear_joiner_first_expedition_skill_level_5: 3000
    unlock_new_skill_cap: 1500
    reach_core_lineup_level_cap: 1000

  resource_rarity_penalty:
    mythic_general_shards: 3000
    widgets: 2500
    gems_below_reserve: 5000
    expedition_manuals: 1000
    hero_xp: 400
```

---

## Candidate generation rules

```yaml
candidate_generation:
  level_up:
    horizon: 1
    heroes:
      include_tags:
        - core
        - active_lineup
      exclude_tags:
        - joiner_only_after_drill

  skill_up:
    horizon: 1
    include:
      - core_hero_skills
      - bear_joiner_first_expedition_skill
    expand_until_threshold:
      enabled: true
      max_extra_steps: 2
      thresholds:
        - skill_level: 5

  star_tier_up:
    horizon: 1
    mode: next_tier_only

  gear:
    include_assignments: true
    include_enhancement: false_until_hero_gear_unlocked
```

---

## Minimal Python skeleton

```python
from dataclasses import dataclass, field
from typing import dict, list
from ortools.sat.python import cp_model


@dataclass(frozen=True)
class Candidate:
    id: str
    action: str
    hero: str
    score: int
    costs: dict[str, int]
    group: str | None = None
    requires: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SolverResult:
    selected: list[Candidate]
    objective_value: int
    status: str


class ORToolsUpgradeOptimizer:
    def __init__(self, time_limit_seconds: float = 0.25, workers: int = 4):
        self.time_limit_seconds = time_limit_seconds
        self.workers = workers

    def select(
        self,
        candidates: list[Candidate],
        capacities: dict[str, int],
        implications: list[tuple[str, str]] | None = None,
        mutex_groups: dict[str, list[str]] | None = None,
    ) -> SolverResult:
        model = cp_model.CpModel()
        x = {c.id: model.NewBoolVar(c.id) for c in candidates}
        by_id = {c.id: c for c in candidates}

        # Resource capacities
        for resource, capacity in capacities.items():
            model.Add(
                sum(c.costs.get(resource, 0) * x[c.id] for c in candidates)
                <= capacity
            )

        # Prefix / dependency constraints: child => parent
        for child_id, parent_id in implications or []:
            if child_id in x and parent_id in x:
                model.Add(x[child_id] <= x[parent_id])

        # Mutex groups: choose at most one command from each group
        for _, ids in (mutex_groups or {}).items():
            ids = [cid for cid in ids if cid in x]
            if ids:
                model.Add(sum(x[cid] for cid in ids) <= 1)

        model.Maximize(sum(c.score * x[c.id] for c in candidates))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_seconds
        solver.parameters.num_search_workers = self.workers

        status_code = solver.Solve(model)
        status = solver.StatusName(status_code)

        selected = [c for c in candidates if solver.BooleanValue(x[c.id])]
        selected.sort(key=lambda c: c.score, reverse=True)

        return SolverResult(
            selected=selected,
            objective_value=int(solver.ObjectiveValue()) if selected else 0,
            status=status,
        )
```

---

## First test fixture

```python
candidates = [
    Candidate(
        id="molly_level_31_to_32",
        action="level_up",
        hero="molly",
        score=5200,
        costs={"hero_xp": 24000},
    ),
    Candidate(
        id="sergey_level_30_to_31",
        action="level_up",
        hero="sergey",
        score=3600,
        costs={"hero_xp": 22000},
    ),
    Candidate(
        id="jessie_expedition_skill_1_4_to_5",
        action="skill_up",
        hero="jessie",
        score=6400,
        costs={"epic_expedition_manual": 10},
    ),
    Candidate(
        id="bahiti_star_2_0_to_2_1",
        action="star_tier_up",
        hero="bahiti",
        score=4100,
        costs={"bahiti_shards": 5},
    ),
]

capacities = {
    "hero_xp": 40000,
    "epic_expedition_manual": 14,
    "bahiti_shards": 14,
}

result = ORToolsUpgradeOptimizer().select(candidates, capacities)

for command in result.selected:
    print(command.id, command.score, command.costs)
```

Expected behavior:

```text
jessie_expedition_skill_1_4_to_5
bahiti_star_2_0_to_2_1
molly_level_31_to_32 OR sergey_level_30_to_31, depending on remaining XP and objective
```

---

## Implementation order

```text
1. Define Candidate model
2. Define Resource capacities
3. Implement hard-rule pruning before solver
4. Implement ORToolsUpgradeOptimizer
5. Add score_model with integer output
6. Add YAML loaders
7. Add dry-run command output
8. Add executor only after dry-run decisions look sane
```

Important: even with OR-Tools-first, do not let solver see unsafe commands. Dangerous actions should be removed before model building, not merely penalized.

