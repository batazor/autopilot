"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { FishPlanPanel } from "@/components/fish/FishPlanPanel";
import { InferenceControl } from "@/components/inference/InferenceControl";
import {
  FleetContextProvider,
  useFleet,
} from "@/components/FleetContextProvider";
import { useApiOffline } from "@/components/ApiStatusProvider";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { fetchInferenceStatus } from "@/lib/api";
import type { FishPlanResult } from "@/lib/types";

function FishDetectPageContent() {
  const { instanceId, instancesError } = useFleet();
  // When the API is globally down, the header "API offline" indicator covers it
  // — don't also surface the instances fetch error as a page banner.
  const apiOffline = useApiOffline();
  // The plan panel is the single live view; it bubbles its result up here so the
  // header can show the fish count + model.
  const [result, setResult] = useState<FishPlanResult | null>(null);

  // Shared with the InferenceControl widget (same query key → one fetch, one
  // cache). Live detection is gated on the sidecar actually being Ready, so the
  // page never fires a doomed request — or double-reports "not running", which
  // the control already shows with a Start button.
  const inferenceStatusQuery = useQuery({
    queryKey: ["inferenceStatus"],
    queryFn: fetchInferenceStatus,
    refetchInterval: (query) => (query.state.data?.ready ? 6000 : 2500),
  });
  const inferenceReady = inferenceStatusQuery.data?.ready ?? false;

  return (
    <>
      <PageHeader title="Fish detect" fleet>
        {result ? (
          <span
            className={`status-pill ${result.available ? "status-idle" : "pill-stale"}`}
            title="Detections found on this frame"
          >
            {result.available
              ? `${result.detections.length} fish · model ${result.model_id}`
              : "inference unavailable"}
          </span>
        ) : null}
      </PageHeader>

      {/* The control is the gate: it shows install / start / ready + logs and a
          Start button. Everything below is live detection, which needs it Ready,
          so it leads the page. */}
      <div style={{ marginBottom: "1rem" }}>
        <InferenceControl />
      </div>

      {instancesError && !apiOffline ? (
        <div className="error-banner">{instancesError}</div>
      ) : null}

      {instanceId ? (
        <FishPlanPanel
          instanceId={instanceId}
          inferenceReady={inferenceReady}
          onResult={setResult}
        />
      ) : null}
    </>
  );
}

export default function FishDetectPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <FleetContextProvider>
        <FishDetectPageContent />
      </FleetContextProvider>
    </Suspense>
  );
}
