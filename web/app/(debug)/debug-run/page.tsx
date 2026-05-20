"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { AppListbox } from "@/components/headless";
import { useFleet } from "@/components/FleetContextProvider";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { fetchModuleScenarios, fetchWikiScopes, runDebugScenario } from "@/lib/api";
import { playerSelectPlaceholder } from "@/lib/fleet-select";
import type { ScenarioRow } from "@/lib/config-pages";
import type { WikiScope } from "@/lib/wiki";

function DebugRunPageInner() {
  const searchParams = useSearchParams();
  const [scopes, setScopes] = useState<WikiScope[]>([]);
  const [scope, setScope] = useState("all");
  const { players, instanceId, playerId, setPlayerId, playersLoading } = useFleet();
  const { showSuccess } = useFeedback();
  const [scenarios, setScenarios] = useState<ScenarioRow[]>([]);
  const [scenarioKey, setScenarioKey] = useState("");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const scenarioQuery = searchParams.get("scenario");
  const scopeQuery = searchParams.get("scope");

  useEffect(() => {
    fetchWikiScopes().then(setScopes).catch(() => {});
  }, []);

  useEffect(() => {
    if (scopeQuery?.trim()) setScope(scopeQuery.trim());
  }, [scopeQuery]);

  useEffect(() => {
    fetchModuleScenarios(scope)
      .then((sc) => {
        setScenarios(sc);
        if (scenarioQuery?.trim()) {
          const q = scenarioQuery.trim();
          const hit = sc.find(
            (s) => s.key === q || s.key.endsWith(`/${q}`) || s.path.endsWith(`/${q}.yaml`),
          );
          if (hit) setScenarioKey(hit.key);
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [scope, scenarioQuery]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return scenarios;
    return scenarios.filter(
      (s) =>
        s.key.toLowerCase().includes(q) ||
        s.name.toLowerCase().includes(q) ||
        s.source.toLowerCase().includes(q),
    );
  }, [scenarios, filter]);

  const selected = scenarios.find((s) => s.key === scenarioKey);

  const run = useCallback(async () => {
    if (!instanceId || !scenarioKey) return;
    setBusy(true);
    setError(null);
    try {
      const res = await runDebugScenario({
        instance_id: instanceId,
        scenario_key: scenarioKey,
        player_id: selected?.device_level ? "" : playerId,
      });
      showSuccess(`Task queued (${res.task_id})`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [instanceId, scenarioKey, playerId, selected, showSuccess]);

  return (
    <>
      <FleetPageHeader title="DSL runner">
        <p className="muted">
          Enqueue a module scenario on an instance queue.{" "}
          <Link href="/edit-dsl">Open DSL editor</Link>
          {" · "}
          <Link href="/modules">Modules</Link>
        </p>
      </FleetPageHeader>
      <section className="panel">
        <div className="toolbar">
          <AppListbox
            inline
            label="Scope"
            value={scope}
            onChange={setScope}
            options={[
              ...(scopes.length
                ? scopes.map((s) => ({ value: s.key, label: s.label }))
                : [{ value: "all", label: "All" }]),
            ]}
            minWidth={140}
          />
          {!selected?.device_level && (
            <AppListbox
              inline
              label="Player"
              value={playerId}
              onChange={setPlayerId}
              loading={playersLoading}
              disabled={playersLoading}
              placeholder={playerSelectPlaceholder(
                playersLoading,
                !playersLoading && players.length === 0,
              )}
              options={[
                { value: "", label: "(device-level)" },
                ...players.map((p) => ({ value: p, label: p })),
              ]}
              minWidth={200}
            />
          )}
          <label>
            Filter
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="key, module path…"
            />
          </label>
          <button
            type="button"
            className="btn-primary"
            disabled={busy || !scenarioKey}
            onClick={run}
          >
            Run on queue
          </button>
        </div>
        <ErrorBanner message={error} />
        <div className="data-table-wrap" style={{ maxHeight: "50vh", overflow: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th />
                <th>Key</th>
                <th>Name</th>
                <th>Module</th>
                <th>Steps</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 200).map((s) => (
                <tr key={s.key}>
                  <td>
                    <input
                      type="radio"
                      name="scenario"
                      checked={scenarioKey === s.key}
                      onChange={() => setScenarioKey(s.key)}
                    />
                  </td>
                  <td>
                    <code>{s.key}</code>
                  </td>
                  <td>{s.name}</td>
                  <td className="muted">{s.source}</td>
                  <td>{s.steps}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {filtered.length > 200 && (
          <p className="muted">Showing first 200 of {filtered.length}</p>
        )}
      </section>
    </>
  );
}

export default function DebugRunPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <DebugRunPageInner />
    </Suspense>
  );
}
