"use client";

import { Chip, Pill, type PillTone } from "@/components/ui";
import type { PlannerResult as Result } from "@/lib/api";

/** Render any nested planner value as a compact human string. */
function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.map(fmt).join(", ");
  if (typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `${k}: ${fmt(val)}`)
      .join(" · ");
  }
  if (typeof v === "number") {
    return Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/\.?0+$/, "");
  }
  return String(v);
}

const REASON_TONE: Record<string, PillTone> = {
  selected: "live",
  goal_reached: "ok",
  all_maxed: "ok",
  goal_unknown: "stale",
  rc_gated: "stale",
  locked: "stale",
  blocked: "danger",
  insufficient_resources: "danger",
  insufficient_stamina: "danger",
  quota_full: "stale",
  none: "neutral",
};

function isObjArray(v: unknown): v is Record<string, unknown>[] {
  return Array.isArray(v) && v.length > 0 && v.every((x) => x && typeof x === "object" && !Array.isArray(x));
}

function Table({ rows }: { rows: Record<string, unknown>[] }) {
  const cols = Array.from(
    rows.reduce((acc, row) => {
      Object.keys(row).forEach((k) => acc.add(k));
      return acc;
    }, new Set<string>()),
  );
  return (
    <div className="overflow-x-auto rounded-lg border border-wos-border-subtle">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-wos-panel-raised text-left text-xs uppercase tracking-wide text-wos-text-muted">
            {cols.map((c) => (
              <th key={c} className="px-3 py-2 font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-t border-wos-border-subtle">
              {cols.map((c) => (
                <td key={c} className="px-3 py-2 align-top text-wos-text">
                  {fmt(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-wos-text-muted">
        {title}
      </h3>
      {children}
    </div>
  );
}

const PRIMARY_KEYS = ["step", "picks", "batch", "commits"];
const SCALAR_ORDER = [
  "reason",
  "detail",
  "affordable",
  "total_cost",
  "reserve",
  "stamina_short",
];

export function PlannerResult({ result }: { result: Result }) {
  const entries = Object.entries(result);

  const scalars = entries.filter(
    ([, v]) => v === null || (typeof v !== "object" && typeof v !== "undefined"),
  );
  const primaryObjs = entries.filter(
    ([k, v]) =>
      PRIMARY_KEYS.includes(k) &&
      ((v && typeof v === "object" && !Array.isArray(v)) || isObjArray(v)),
  );
  const tables = entries.filter(
    ([k, v]) => !PRIMARY_KEYS.includes(k) && isObjArray(v),
  );
  const objects = entries.filter(
    ([k, v]) =>
      !PRIMARY_KEYS.includes(k) &&
      v &&
      typeof v === "object" &&
      !Array.isArray(v),
  );
  const primArrays = entries.filter(
    ([k, v]) => !PRIMARY_KEYS.includes(k) && Array.isArray(v) && !isObjArray(v) && v.length > 0,
  );

  const reason = result.reason as string | undefined;

  scalars.sort(
    (a, b) =>
      (SCALAR_ORDER.indexOf(a[0]) + 1 || 99) - (SCALAR_ORDER.indexOf(b[0]) + 1 || 99),
  );

  return (
    <div className="flex flex-col gap-5">
      {/* Headline: reason pill + scalar chips */}
      <div className="flex flex-wrap items-center gap-2">
        {reason ? (
          <Pill tone={REASON_TONE[reason] ?? "neutral"} dot>
            {reason}
          </Pill>
        ) : null}
        {scalars
          .filter(([k]) => k !== "reason")
          .map(([k, v]) => (
            <Chip key={k}>
              {k}: {fmt(v)}
            </Chip>
          ))}
      </div>

      {/* Primary recommendation(s) */}
      {primaryObjs.map(([k, v]) => (
        <Section key={k} title={k === "step" ? "Recommended next" : k}>
          {isObjArray(v) ? (
            <Table rows={v} />
          ) : v && typeof v === "object" ? (
            <div className="rounded-lg border border-wos-border-subtle bg-wos-panel-raised px-3 py-2 text-sm text-wos-text">
              {fmt(v)}
            </div>
          ) : (
            <span className="text-sm text-wos-text-muted">—</span>
          )}
        </Section>
      ))}

      {/* Object fields (remaining balances, etc.) */}
      {objects.map(([k, v]) => (
        <Section key={k} title={k}>
          <div className="flex flex-wrap gap-2">
            {Object.entries(v as Record<string, unknown>).map(([ik, iv]) => (
              <Chip key={ik}>
                {ik}: {fmt(iv)}
              </Chip>
            ))}
          </div>
        </Section>
      ))}

      {/* Primitive arrays (chain, bottleneck_resources, …) */}
      {primArrays.map(([k, v]) => (
        <Section key={k} title={k}>
          <div className="flex flex-wrap gap-2">
            {(v as unknown[]).map((iv, i) => (
              <Chip key={i}>{fmt(iv)}</Chip>
            ))}
          </div>
        </Section>
      ))}

      {/* Tables (candidates, starved, no_channel, …) */}
      {tables.map(([k, v]) => (
        <Section key={k} title={k}>
          <Table rows={v as Record<string, unknown>[]} />
        </Section>
      ))}
    </div>
  );
}
