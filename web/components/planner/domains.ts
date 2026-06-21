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
        help: 'pet_id → { "refine": int, "skill": int }. Empty = none invested.',
      },
      {
        key: "resources",
        kind: "json",
        label: "Resource balances",
        default: J({ pet_food: 100 }),
        help: "pet_shard:<pet_id> and pet_food.",
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
