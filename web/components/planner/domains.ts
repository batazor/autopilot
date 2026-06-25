import type { PlannerDomain } from "@/lib/api";

/**
 * One input control on a planner form. The live state readers are deferred, so
 * the operator supplies the player state here and the backend runs the planner.
 */
export type PlannerField =
  | {
      key: string;
      kind: "number";
      label: string;
      default: number | "";
      help?: string;
      /** Empty value is omitted from the request body (planner default applies). */
      optional?: boolean;
    }
  | { key: string; kind: "text"; label: string; default: string; help?: string }
  | { key: string; kind: "bool"; label: string; default: boolean; help?: string }
  | { key: string; kind: "role"; label: string; help?: string }
  | { key: string; kind: "json"; label: string; default: string; help?: string }
  | {
      key: string;
      kind: "building_levels";
      label: string;
      default: string;
      help?: string;
    };

export type PlannerDomainConfig = {
  id: PlannerDomain;
  label: string;
  blurb: string;
  fields: PlannerField[];
};

const J = (v: unknown) => JSON.stringify(v, null, 2);

export const PLANNER_DOMAINS: PlannerDomainConfig[] = [
  {
    id: "building",
    label: "Building",
    blurb:
      "Furnace-first: which buildings to upgrade next, value-ranked across the free construction queues.",
    fields: [
      { key: "goal_id", kind: "text", label: "Goal building", default: "furnace" },
      { key: "goal_cap", kind: "number", label: "Goal cap (level)", default: 30 },
      { key: "free_queues", kind: "number", label: "Free queues", default: 2 },
      { key: "role", kind: "role", label: "Role" },
      {
        key: "levels",
        kind: "building_levels",
        label: "Current building levels",
        default: J({ furnace: 10 }),
        help: "Set a building's level; Auto-fill backfills the prerequisites it implies.",
      },
      {
        key: "resources",
        kind: "json",
        label: "Resource balances",
        default: "null",
        help: "item_id → amount. null = ignore affordability gating.",
      },
    ],
  },
  {
    id: "research",
    label: "Research",
    blurb:
      "Value-greedy tech pick: extra march queue + compounding speeds first, value inherited up prereq chains.",
    fields: [
      { key: "rc_level", kind: "number", label: "Research Center level", default: 10 },
      { key: "role", kind: "role", label: "Role" },
      {
        key: "levels",
        kind: "json",
        label: "Current research levels",
        default: J({}),
        help: "node_id → level (int). 0 / missing = not researched.",
      },
      {
        key: "target_levels",
        kind: "json",
        label: "Target levels (roadmap)",
        default: J({}),
        help: "node_id → target level. Set any → returns a roadmap (total cost/time/power). Empty = next-tech pick only.",
      },
      {
        key: "research_speed_pct",
        kind: "number",
        label: "Research speed %",
        default: "",
        optional: true,
        help: "State buff +10%, VP +10% / SVP +15%, hero/RC bonuses — shortens the roadmap time.",
      },
    ],
  },
  {
    id: "heroes",
    label: "Heroes",
    blurb:
      "Value-greedy hero investment gated by server generation, costed in shards + rarity books.",
    fields: [
      {
        key: "current_generation",
        kind: "number",
        label: "Server generation",
        default: 5,
        optional: true,
      },
      { key: "role", kind: "role", label: "Role" },
      {
        key: "owned",
        kind: "json",
        label: "Owned heroes",
        default: J({}),
        help: 'hero_id → { "star": int, "skill": int }. Empty = none invested.',
      },
      {
        key: "resources",
        kind: "json",
        label: "Resource balances",
        default: J({ "book:mythic": 50, "book:epic": 50 }),
        help: "shard:<hero_id> and book:mythic / book:epic / book:rare.",
      },
    ],
  },
  {
    id: "pets",
    label: "Pets",
    blurb:
      "Value-greedy pet investment gated by server age (days + prereq pet), costed in pet shards + pet food.",
    fields: [
      {
        key: "server_days",
        kind: "number",
        label: "Server age (days)",
        default: 200,
        optional: true,
      },
      { key: "role", kind: "role", label: "Role" },
      {
        key: "owned",
        kind: "json",
        label: "Owned pets",
        default: J({}),
        help: 'pet_id → { "level": int, "refine": int, "skill": int }. Empty = none invested.',
      },
      {
        key: "resources",
        kind: "json",
        label: "Resource balances",
        default: J({ pet_food: 100 }),
        help: "pet_shard:<pet_id> and pet_food.",
      },
      {
        key: "target_levels",
        kind: "json",
        label: "Target levels (roadmap)",
        default: J({}),
        help: "pet_id → target level. Set any → roadmap: advancement materials (Taming Manual / Energizing Potion / Strengthening Serum) + advancement score (→ SvS Day-3/5 pts) + at-max troop ATK/DEF % for pets taken to max. Per-pet max is 50/60/70/80/100. Empty = next-pet pick only.",
      },
    ],
  },
  {
    id: "intel",
    label: "Intel",
    blurb:
      "Which markers to clear this refresh, ranked by loot value, spending stamina − reserve best-first.",
    fields: [
      { key: "stamina", kind: "number", label: "Stamina", default: 50, optional: true },
      { key: "reserve", kind: "number", label: "Reserve", default: 10 },
      { key: "cost_per_event", kind: "number", label: "Cost / event", default: 10 },
      {
        key: "daily_quota_left",
        kind: "number",
        label: "Daily quota left",
        default: "",
        optional: true,
        help: "Empty = unlimited.",
      },
      {
        key: "events",
        kind: "json",
        label: "Detected markers",
        default: J([
          { kind: "fight", color: "gold", score: 0.95 },
          { kind: "skull", color: "purple", score: 0.8 },
          { kind: "beast", color: "blue", score: 0.7 },
        ]),
        help: "Each: { kind, color, score }.",
      },
    ],
  },
  {
    id: "coordinator",
    label: "Coordinator",
    blurb:
      "The brain: arbitrates the shared resource pool across idle execution channels under one objective.",
    fields: [
      {
        key: "balances",
        kind: "json",
        label: "Shared balances",
        default: J({ wood: 8000, meat: 20000, coal: 5000 }),
      },
      {
        key: "channels",
        kind: "json",
        label: "Idle channels",
        default: J([
          { id: "construction_1", kind: "construction" },
          { id: "research_1", kind: "research" },
        ]),
      },
      {
        key: "candidates",
        kind: "json",
        label: "Candidate actions",
        default: J([
          {
            domain: "building",
            channel_kind: "construction",
            key: "furnace",
            priority: 850,
            cost: { wood: 5000 },
          },
          {
            domain: "research",
            channel_kind: "research",
            key: "tactics",
            priority: 900,
            cost: { meat: 10000 },
          },
        ]),
      },
    ],
  },
];

PLANNER_DOMAINS.push(
  {
    id: "safety",
    label: "Safety",
    blurb:
      "Defensive directive from the current threat: shield / recall, and which domains to suppress.",
    fields: [
      { key: "incoming_attack", kind: "bool", label: "Incoming attack", default: false },
      { key: "attack_eta_s", kind: "number", label: "Attack ETA (s)", default: 0 },
      { key: "shield_active", kind: "bool", label: "Shield active", default: false },
      {
        key: "shield_remaining_s",
        kind: "number",
        label: "Shield remaining (s)",
        default: 0,
      },
      { key: "pvp_window", kind: "bool", label: "PvP window (SvS/KE)", default: false },
      { key: "troops_exposed", kind: "bool", label: "Troops on the map", default: false },
      {
        key: "gatherers_under_attack",
        kind: "bool",
        label: "Gatherers under attack",
        default: false,
      },
      { key: "injured", kind: "number", label: "Injured troops", default: 0 },
    ],
  },
  {
    id: "chief_orders",
    label: "Chief Orders",
    blurb: "Order the chief orders by fit to the active event / situation.",
    fields: [
      {
        key: "active_categories",
        kind: "json",
        label: "Active event categories",
        default: J(["construction", "research"]),
        help: "From the calendar bias, e.g. construction / research / training.",
      },
      { key: "injured", kind: "number", label: "Injured troops", default: 0 },
      { key: "pvp_window", kind: "bool", label: "PvP window", default: false },
    ],
  },
  {
    id: "speedups",
    label: "Speedups",
    blurb: "Apply the speedup inventory to the longest-running tasks, category-matched first.",
    fields: [
      {
        key: "tasks",
        kind: "json",
        label: "Running tasks",
        default: J([
          { id: "furnace", category: "construction", remaining_s: 7200 },
          { id: "tactics", category: "research", remaining_s: 3600 },
        ]),
        help: "Each: { id, category, remaining_s }.",
      },
      {
        key: "inventory_minutes",
        kind: "json",
        label: "Speedup inventory (minutes)",
        default: J({ construction: 120, research: 60, universal: 90 }),
        help: "Minutes available per category (+ universal).",
      },
      { key: "spend_now", kind: "bool", label: "Spend now", default: true },
    ],
  },
  {
    id: "currency",
    label: "Currency",
    blurb: "Spend a premium-currency balance on the best-ROI sinks first.",
    fields: [
      { key: "balance", kind: "number", label: "Balance", default: 2000 },
      { key: "currency", kind: "text", label: "Currency", default: "diamonds" },
      {
        key: "sinks",
        kind: "json",
        label: "Sinks",
        default: J([
          { id: "hero_shard", currency: "diamonds", cost: 500, value: 9 },
          { id: "speedup_pack", currency: "diamonds", cost: 300, value: 4 },
        ]),
        help: "Each: { id, currency, cost, value, available? }. Higher value = better ROI.",
      },
    ],
  },
  {
    id: "svs",
    label: "SvS",
    blurb:
      "SvS prep-phase points: score a planned spend across the 5 themed days (points-per-item from wostools).",
    fields: [
      {
        key: "plan",
        kind: "json",
        label: "Planned spend",
        default: J({ "4": { mithril: 3 }, "3": { polar_terror_rally: 5 } }),
        help: "day (1-5) → { activity: qty }. Activities: mithril, refined_fire_crystal_building, polar_terror_rally, hero_widget, hero_gear_essence_stone, fire_crystal_building, fc_shard_research, *_speedup_min, chief_charm_score, chief_gear_score, hero_shard_{rare,epic,mythic}, wild_mark_{advanced,common}, pet_advancement_score, expert_sigil, book_of_knowledge, lucky_wheel, beast_l*. Wrong-day entries are reported in 'unknown'.",
      },
      {
        key: "troops",
        kind: "json",
        label: "Day-4 troops (optional)",
        default: J([]),
        help: 'Each: { "action": "train"|"promote", "qty": int, "tier"?: int, "from_tier"?: int, "to_tier"?: int }. Tier table is partial (T10/T11).',
      },
      {
        key: "target",
        kind: "number",
        label: "Target score",
        default: "",
        optional: true,
        help: "Optional personal goal → adds remaining + % progress.",
      },
    ],
  },
  {
    id: "rfc",
    label: "RFC",
    blurb:
      "Fire Crystal → Refined Fire Crystal weekly conversion: FC budget + weeks to net a target RFC (expected value, Tier-1 efficient pace).",
    fields: [
      {
        key: "target_rfc",
        kind: "number",
        label: "Target RFC",
        default: 29,
        optional: true,
        help: "How many Refined Fire Crystals you want → returns FC needed + weeks (efficient Tier-1 pace, ~14 FC/RFC). Empty = just the tier table + weekly yield.",
      },
      {
        key: "with_discount",
        kind: "bool",
        label: "Daily 50% discount",
        default: true,
        help: "Apply the once-per-day half-cost conversion (7/week).",
      },
      {
        key: "conversions",
        kind: "number",
        label: "Conversions (advanced)",
        default: "",
        optional: true,
        help: "EV of doing N conversions starting at the weekly index below (push-through-tiers view).",
      },
      {
        key: "start_index",
        kind: "number",
        label: "Start index (0-99)",
        default: 0,
        help: "Conversions already done this week (positions you in the right tier).",
      },
    ],
  },
  {
    id: "koi",
    label: "KoI",
    blurb:
      "King of the Icefield points: score a planned spend across the 7 themed days (points-per-item from wostools).",
    fields: [
      {
        key: "plan",
        kind: "json",
        label: "Planned spend",
        default: J({ "2": { mithril: 2 }, "7": { chief_gear_score: 10 } }),
        help: "day (1-7) → { activity: qty }. Days: 1 City Construction, 2 Hero Dev, 3 Basic Skills, 4 Combat Training, 5 Basic Skills (again), 6 Combat Training (again), 7 Hero Dev (again). Activities: mithril, refined_fire_crystal_building, fire_crystal_building, fc_shard_research, *_speedup_min, chief_charm_score, chief_gear_score, hero_widget, hero_gear_essence_stone, hero_shard_{rare,epic,mythic}, wild_mark_{advanced,common}, pet_advancement_score, expert_sigil, book_of_knowledge, lucky_wheel, gather_per_batch. Wrong-day entries are reported in 'unknown'.",
      },
      {
        key: "troops",
        kind: "json",
        label: "Troops (Days 4 & 6, optional)",
        default: J([]),
        help: 'Each: { "action": "train"|"promote", "qty": int, "day": 4|6, "tier"?: int, "from_tier"?: int, "to_tier"?: int }. KoI exposes no per-tier point table → troops report as unknown.',
      },
      {
        key: "target",
        kind: "number",
        label: "Target score",
        default: "",
        optional: true,
        help: "Optional Medal-of-Honor goal → adds remaining + % progress.",
      },
    ],
  },
  {
    id: "alliance_showdown",
    label: "Alliance Showdown",
    blurb:
      "Alliance Showdown points: score a planned spend across the 6 themed stages (points-per-item from wostools), with the Baldur +5%/level bonus.",
    fields: [
      {
        key: "plan",
        kind: "json",
        label: "Planned spend",
        default: J({ "4": { mithril: 3 }, "1": { refined_fire_crystal_building: 5 } }),
        help: "stage (1-6) → { activity: qty }. Stages: 1 Rise of the City, 2 Hero Development, 3 Pet Training, 4 Gear Enhancement, 5 Trade Dominion, 6 Full-Scale Competition (all activities). Activities: mithril, refined_fire_crystal_building, fire_crystal_building, fc_shard_research, hero_widget, hero_gear_essence_stone, hero_shard_{rare,epic,mythic}, wild_mark_{advanced,common}, pet_advancement_score, chief_charm_score, chief_gear_score, escort_truck, raid_truck, expert_sigil, book_of_knowledge, *_speedup_min, gather_*, gem. Wrong-stage entries are reported in 'unknown'.",
      },
      {
        key: "baldur",
        kind: "json",
        label: "Baldur level (optional)",
        default: J({}),
        help: 'stage → Baldur level (1-6); each adds +5%/level to that stage. e.g. { "4": 6 } lifts Stage-4 lines ×1.30. Empty = no bonus.',
      },
      {
        key: "troops",
        kind: "json",
        label: "Troops (Stages 4 & 6, optional)",
        default: J([]),
        help: 'Each: { "action": "train"|"promote", "qty": int, "stage": 4|6, "tier"?: int, "from_tier"?: int, "to_tier"?: int }. The per-tier point table is unsourced → troops report as unknown.',
      },
      {
        key: "target",
        kind: "number",
        label: "Target score",
        default: "",
        optional: true,
        help: "Optional personal goal → adds remaining + % progress (personal ranking floor is 300,000).",
      },
    ],
  },
  {
    id: "tower_capture",
    label: "Tower Capture",
    blurb:
      "Which Sunfire Castle buff towers to help capture, ranked by value = buff % × your role's fit for the buff type × proximity to the castle (74 towers, 8 types, from wostools).",
    fields: [
      { key: "role", kind: "role", label: "Account role" },
      {
        key: "target_count",
        kind: "number",
        label: "Top N picks",
        default: 5,
        help: "0 = rank all 74 towers.",
      },
      {
        key: "controlled",
        kind: "json",
        label: "Towers already held",
        default: J({}),
        help: 'tower_id → true (e.g. { "tech_l1_0": true }). Held towers are excluded from the picks. IDs are "<buff_type>_l<level>_<index>".',
      },
    ],
  },
  {
    id: "vip",
    label: "VIP",
    blurb:
      "VIP progression VIP 1 → 12: the next level-up + how many VIP Points (10/100/1k/10k) to reach a target (ladder from wostools; 1:1 points→XP).",
    fields: [
      {
        key: "current_level",
        kind: "number",
        label: "Current VIP level",
        default: 1,
        help: "1–12. 0 (unread) is treated as the VIP-1 base.",
      },
      {
        key: "current_xp",
        kind: "number",
        label: "XP into current level",
        default: 0,
        optional: true,
        help: "VIP XP already banked toward the next level.",
      },
      {
        key: "resources",
        kind: "json",
        label: "Budget",
        default: J({ vip_points: 100000 }),
        help: "{ vip_points: n } — VIP Points on hand (apply 1:1 as VIP XP).",
      },
      { key: "role", kind: "role", label: "Role" },
      {
        key: "target_level",
        kind: "number",
        label: "Target level (roadmap)",
        default: 12,
        optional: true,
        help: "Set → returns total XP + the VIP Points item breakdown to reach it.",
      },
    ],
  },
);

export function domainById(id: string): PlannerDomainConfig | undefined {
  return PLANNER_DOMAINS.find((d) => d.id === id);
}

/**
 * Build the request body from the current raw field values. Numbers are parsed
 * (optional + empty → omitted), JSON fields are parsed, role/text pass through.
 * Throws on invalid JSON so the form can surface which field is broken.
 */
/**
 * Inverse of {@link buildBody}: turn a planner input body (from the player's
 * saved state) into raw form field values. Only keys present in the body are
 * returned, so unset fields keep their form defaults when merged.
 */
export function valuesFromBody(
  cfg: PlannerDomainConfig,
  body: Record<string, unknown>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of cfg.fields) {
    if (!(f.key in body)) continue;
    const v = body[f.key];
    if (v === null || v === undefined) continue;
    if (f.kind === "json" || f.kind === "building_levels") {
      out[f.key] = JSON.stringify(v, null, 2);
    } else if (f.kind === "bool") {
      out[f.key] = v ? "true" : "false";
    } else {
      out[f.key] = String(v);
    }
  }
  return out;
}

export function buildBody(
  cfg: PlannerDomainConfig,
  values: Record<string, string>,
): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  for (const f of cfg.fields) {
    const raw = values[f.key] ?? "";
    if (f.kind === "number") {
      if (raw.trim() === "") {
        if (f.optional) continue;
        body[f.key] = 0;
      } else {
        const n = Number(raw);
        if (Number.isNaN(n)) throw new Error(`${f.label}: not a number`);
        body[f.key] = n;
      }
    } else if (f.kind === "json" || f.kind === "building_levels") {
      try {
        body[f.key] = JSON.parse(raw);
      } catch (e) {
        throw new Error(`${f.label}: invalid JSON (${(e as Error).message})`);
      }
    } else if (f.kind === "bool") {
      body[f.key] = raw === "true";
    } else if (f.kind === "role") {
      if (raw.trim() !== "") body[f.key] = raw;
    } else {
      body[f.key] = raw;
    }
  }
  return body;
}
