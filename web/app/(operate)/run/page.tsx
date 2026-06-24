"use client";

import { Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppListbox } from "@/components/headless";
import { useFleet } from "@/components/FleetContextProvider";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { ScenarioFocusControl } from "@/components/ScenarioFocusControl";
import { fetchModuleScenarios } from "@/lib/api";

function RunPageInner() {
  const { instanceId, players } = useFleet();
  const [scenarioKey, setScenarioKey] = useState("");
  const [player, setPlayer] = useState("");

  const scenariosQuery = useQuery({
    queryKey: ["moduleScenarios", "all"],
    queryFn: () => fetchModuleScenarios("all"),
  });

  const scenarios = useMemo(
    () =>
      [...(scenariosQuery.data ?? [])].sort((a, b) =>
        a.key.localeCompare(b.key),
      ),
    [scenariosQuery.data],
  );
  const selected = useMemo(
    () => scenarios.find((s) => s.key === scenarioKey) ?? null,
    [scenarios, scenarioKey],
  );
  const needsPlayer = Boolean(selected && !selected.device_level);
  const ready =
    Boolean(instanceId) &&
    Boolean(scenarioKey) &&
    (!needsPlayer || Boolean(player));

  return (
    <main>
      <PageHeader title="Run scenario" fleet>
        Launch any scenario point-wise in <strong>focus mode</strong> — the
        device runs <strong>only</strong> the chosen scenario, with no scheduler,
        no other instances and no autonomous overlay/identity work.
      </PageHeader>

      <div className="panel" style={{ display: "grid", gap: "0.75rem", maxWidth: 560 }}>
        <AppListbox
          inline
          label="Scenario"
          value={scenarioKey}
          onChange={(v) => setScenarioKey(v)}
          disabled={!scenarios.length}
          options={scenarios.map((s) => ({
            value: s.key,
            label: `${s.name || s.key}  ·  ${s.key}${s.device_level ? "" : "  (account)"}`,
          }))}
          minWidth={320}
        />
        {needsPlayer ? (
          <AppListbox
            inline
            label="Account"
            value={player}
            onChange={(v) => setPlayer(v)}
            disabled={!players.length}
            options={players.map((p) => ({ value: p, label: p }))}
            minWidth={200}
          />
        ) : null}
        {scenariosQuery.isError ? (
          <p style={{ color: "#ef4444", fontSize: "0.85rem" }}>
            Failed to load scenarios.
          </p>
        ) : null}
      </div>

      {ready && selected ? (
        <ScenarioFocusControl
          key={`${instanceId}:${scenarioKey}`}
          instanceId={instanceId}
          scenarioKey={scenarioKey}
          player={needsPlayer ? player : ""}
          title={selected.name || scenarioKey}
        />
      ) : (
        <div className="panel" style={{ maxWidth: 560, opacity: 0.7, fontSize: "0.85rem" }}>
          {!instanceId
            ? "Select an instance in the header to begin."
            : !scenarioKey
              ? "Pick a scenario to launch."
              : "Select an account for this account-level scenario."}
        </div>
      )}
    </main>
  );
}

export default function RunPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <RunPageInner />
    </Suspense>
  );
}
