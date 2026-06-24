"use client";

import { ScenarioFocusControl } from "@/components/ScenarioFocusControl";

// The device-side scenario that plays the Fishing Tournament event. Matches the
// scenario filename stem under games/<game>/events/fishing_tournament/scenarios.
const FISHING_SCENARIO = "event.fishing_tournament";

/**
 * Fishing control card — a thin wrapper over the generic
 * {@link ScenarioFocusControl}. "Play" runs the Fishing Tournament in focus
 * mode: a single-instance worker that runs ONLY this scenario (no scheduler, no
 * other instances, no autonomous work). Play when stopped, Restart + Stop when
 * running.
 */
export function FishPlayControl({ instanceId }: { instanceId: string }) {
  return (
    <ScenarioFocusControl
      instanceId={instanceId}
      scenarioKey={FISHING_SCENARIO}
      title="Fishing control"
      description="Runs only the Fishing Tournament on this device — no scheduler, no other instances."
    />
  );
}
