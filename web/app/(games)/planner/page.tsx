"use client";

import { Suspense, useState } from "react";
import { FleetContextProvider, useFleet } from "@/components/FleetContextProvider";
import { PageHeader } from "@/components/PageHeader";
import { PLANNER_DOMAINS } from "@/components/planner/domains";
import { FleetPlanView } from "@/components/planner/FleetPlanView";
import { FullPlanView } from "@/components/planner/FullPlanView";
import { PlannerForm } from "@/components/planner/PlannerForm";
import { PlannerResult } from "@/components/planner/PlannerResult";
import { usePlanner } from "@/components/planner/usePlanner";
import { Button, Card, PageLoading } from "@/components/ui";

const FULL = "full";
const FLEET = "fleet";

function PlannerInner() {
  const planner = usePlanner();
  const { cfg, domain, setDomain } = planner;
  const { playerId } = useFleet();
  const [tab, setTab] = useState<string>(FLEET);

  return (
    <>
      <PageHeader title="Planners" fleet={{ showPlayer: true }}>
        <p className="muted">
          What-if calculators over the per-domain planners. Pick a player to load
          their saved state, hand-fill what the readers don&apos;t capture yet, then
          compute. Pure compute: no device, nothing is executed.
        </p>
      </PageHeader>

      <div className="toolbar items-center">
        <Button
          variant="secondary"
          pending={planner.syncing}
          disabled={!playerId}
          onClick={() => playerId && planner.loadFromPlayer(playerId)}
          title={playerId ? `Load ${playerId}` : "Select a player first"}
        >
          Load from player
        </Button>
        <Button
          variant="secondary"
          pending={planner.syncing}
          disabled={!playerId}
          onClick={() => playerId && planner.saveToPlayer(playerId)}
          title={playerId ? `Save to ${playerId}` : "Select a player first"}
        >
          Save to player
        </Button>
        {planner.notice ? (
          <span className="text-sm text-wos-text-muted">{planner.notice}</span>
        ) : null}
        {!playerId ? (
          <span className="text-sm text-wos-text-muted">
            Select a player in the header to load / save state.
          </span>
        ) : null}
      </div>

      <div className="toolbar">
        <button
          type="button"
          className={tab === FLEET ? "btn-primary" : "btn-secondary"}
          onClick={() => setTab(FLEET)}
        >
          ⊞ Fleet
        </button>
        <button
          type="button"
          className={tab === FULL ? "btn-primary" : "btn-secondary"}
          onClick={() => setTab(FULL)}
        >
          ★ Full plan
        </button>
        {PLANNER_DOMAINS.map((d) => (
          <button
            key={d.id}
            type="button"
            className={tab === d.id ? "btn-primary" : "btn-secondary"}
            onClick={() => {
              setTab(d.id);
              setDomain(d.id);
            }}
          >
            {d.label}
          </button>
        ))}
      </div>

      {tab === FLEET ? (
        <>
          <p className="mb-3 max-w-3xl text-sm text-wos-text-secondary">
            Runs the full plan for every account at once — one glance at what each
            should do next. Reads each player&apos;s saved state; pure compute.
          </p>
          <FleetPlanView />
        </>
      ) : tab === FULL ? (
        <>
          <p className="mb-3 max-w-3xl text-sm text-wos-text-secondary">
            Runs every domain planner on the current inputs and lets the coordinator
            arbitrate the shared resource pool across the parallel execution
            channels — the unified &quot;what should this account do next&quot;.
          </p>
          <FullPlanView planner={planner} />
        </>
      ) : (
        <>
          <p className="mb-3 max-w-3xl text-sm text-wos-text-secondary">
            {cfg.blurb}
          </p>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
            <Card title="Inputs">
              <PlannerForm
                cfg={cfg}
                values={planner.values}
                meta={planner.meta}
                busy={planner.busy}
                onChange={planner.setValue}
                onCompute={planner.compute}
                onReset={planner.reset}
              />
            </Card>

            <Card title="Recommendation">
              {planner.error ? (
                <p className="error-banner">{planner.error}</p>
              ) : planner.result ? (
                <PlannerResult result={planner.result} />
              ) : (
                <p className="text-sm text-wos-text-muted">
                  Set the inputs and press <strong>Compute</strong>.
                </p>
              )}
            </Card>
          </div>
        </>
      )}
    </>
  );
}

export default function PlannerPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <FleetContextProvider>
        <PlannerInner />
      </FleetContextProvider>
    </Suspense>
  );
}
