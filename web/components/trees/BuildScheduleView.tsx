"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppTabs } from "@/components/headless";
import { BuildPlanGantt } from "@/components/buildings/BuildPlanGantt";
import { fetchBuildPlan } from "@/lib/api";
import type { BuildPlanView } from "@/lib/types";
import { fmtPlanDuration } from "@/lib/trees/format";

// Build schedule as a Gantt. The order/timing come from the real planner
// (`/api/buildings/plan` → planner.project_schedule / project_multi_schedule),
// so this is "what the bot would build" — from scratch or the picked player's
// levels, on one queue (furnace-first critical path) or two (parallel).
export function BuildScheduleView({ playerId }: { playerId: string }) {
  const [queues, setQueues] = useState(2);
  const plan = useQuery<BuildPlanView>({
    queryKey: ["build-plan", playerId, queues],
    queryFn: () => fetchBuildPlan(playerId || undefined, queues),
  });

  const p = plan.data;
  return (
    <div className="flex flex-col gap-3">
      <AppTabs
        variant="toolbar"
        renderPanels={false}
        selectedKey={String(queues)}
        onChange={(k) => setQueues(Number(k))}
        tabs={[
          { key: "1", label: "1 queue", title: "Single queue — the furnace-first critical path" },
          { key: "2", label: "2 queues", title: "Two queues in parallel — economy/camps fill the idle queue" },
        ]}
      />
      {plan.isLoading ? <p className="muted">Planning the build order…</p> : null}
      {plan.error ? (
        <div className="error-banner">
          {plan.error instanceof Error ? plan.error.message : String(plan.error)}
        </div>
      ) : null}
      {p ? (
        <>
          <div className="cost-summary">
            <div className="cost-summary__title">
              Road to {p.goal} {p.goal_cap}
              {p.start_from.startsWith("player:")
                ? ` — from ${p.start_from.slice("player:".length)}'s current levels`
                : " — from scratch"}
            </div>
            <div className="cost-summary__stats">
              <span className="cost-stat" title="Upgrades on this path">
                <span className="cost-stat__icon">🏗️</span>
                {p.step_count} steps
              </span>
              <span className="cost-stat cost-stat--time" title="Total construction time">
                <span className="cost-stat__icon">⏱</span>
                {fmtPlanDuration(p.total_time_s)}
              </span>
            </div>
            <div className="cost-summary__note">
              {p.queues > 1
                ? `Furnace-first on the critical chain, ${p.queues} queues in parallel (economy/camps fill the idle one), no speedups.`
                : "Furnace-first order, one construction queue, no speedups — the spine of how the bot advances."}
              {p.truncated ? " (truncated at the step cap.)" : ""}
            </div>
          </div>
          <BuildPlanGantt plan={p} />
        </>
      ) : null}
    </div>
  );
}
