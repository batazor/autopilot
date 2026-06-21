"use client";

import { useState } from "react";
import { Button, Card, Chip } from "@/components/ui";
import type { PlannerResult } from "@/lib/api";
import { PlannerResult as ResultView } from "./PlannerResult";
import type { usePlanner } from "./usePlanner";

const DEFAULT_BALANCES = JSON.stringify(
  {
    "book:mythic": 50,
    "book:epic": 50,
    "book:rare": 50,
    pet_food: 100,
    meat: 10_000_000,
    wood: 10_000_000,
    coal: 5_000_000,
    iron: 5_000_000,
    steel: 2_000_000,
  },
  null,
  2,
);

type Plan = PlannerResult & { step?: unknown; picks?: unknown };

function pickSummary(plans: Record<string, PlannerResult>): {
  label: string;
  value: string;
}[] {
  const out: { label: string; value: string }[] = [];
  const b = plans.building as Plan | undefined;
  const picks = (b?.picks as Record<string, unknown>[] | undefined) ?? [];
  out.push({
    label: "Building",
    value: picks.length
      ? picks.map((p) => `${p.spec_id}→${p.to_level}`).join(", ")
      : "—",
  });
  const r = (plans.research as Plan | undefined)?.step as
    | Record<string, unknown>
    | undefined;
  out.push({ label: "Research", value: r ? String(r.name) : "—" });
  const h = (plans.heroes as Plan | undefined)?.step as
    | Record<string, unknown>
    | undefined;
  out.push({
    label: "Hero",
    value: h ? `${h.hero_id} ${h.kind}→${h.to_level}` : "—",
  });
  const p = (plans.pets as Plan | undefined)?.step as
    | Record<string, unknown>
    | undefined;
  out.push({
    label: "Pet",
    value: p ? `${p.pet_id} ${p.kind}→${p.to_level}` : "—",
  });
  return out;
}

export function FullPlanView({
  planner,
}: {
  planner: ReturnType<typeof usePlanner>;
}) {
  const [balances, setBalances] = useState(DEFAULT_BALANCES);
  const [parseErr, setParseErr] = useState<string | null>(null);

  const run = () => {
    setParseErr(null);
    let parsed: Record<string, number>;
    try {
      parsed = JSON.parse(balances);
    } catch (e) {
      setParseErr(`Balances: invalid JSON (${(e as Error).message})`);
      return;
    }
    planner.runFull(parsed);
  };

  const res = planner.fullResult;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
      <Card title="Shared resource pool">
        <div className="flex flex-col gap-3">
          <p className="text-xs text-wos-text-muted">
            The coordinator spends this pool across all domains. Building / research
            / hero / pet inputs come from their own tabs — set them there (or Load
            from a player), then run. Lower a resource to see what it starves.
          </p>
          <textarea
            className="field font-mono text-xs"
            rows={11}
            spellCheck={false}
            value={balances}
            onChange={(e) => setBalances(e.target.value)}
          />
          <Button variant="primary" pending={planner.busy} onClick={run}>
            Run full plan
          </Button>
          {parseErr ? <p className="error-banner">{parseErr}</p> : null}
        </div>
      </Card>

      <Card title="Unified plan">
        {planner.fullError ? (
          <p className="error-banner">{planner.fullError}</p>
        ) : res ? (
          <div className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-wos-text-muted">
                Per-domain pick
              </h3>
              <div className="flex flex-wrap gap-2">
                {pickSummary(res.plans).map((s) => (
                  <Chip key={s.label} title={s.value}>
                    {s.label}: {s.value}
                  </Chip>
                ))}
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-wos-text-muted">
                Coordinator decision
              </h3>
              <ResultView result={res.decision} />
            </div>
          </div>
        ) : (
          <p className="text-sm text-wos-text-muted">
            Press <strong>Run full plan</strong> to arbitrate all domains.
          </p>
        )}
      </Card>
    </div>
  );
}
