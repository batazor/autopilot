"use client";

import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { AppListbox, AppSwitch, AppTabs } from "@/components/headless";
import { ErrorBanner, useFeedback } from "@/components/feedback";

const GAME_TABS: { id: string; label: string }[] = [
  { id: "wos", label: "Whiteout Survival" },
  { id: "kingshot", label: "Kingshot" },
];
import { debugRunHref, editDslHref } from "@/lib/debug-links";
import { PageHeader } from "@/components/PageHeader";
import {
  fetchModules,
  fetchPlayerAssignments,
  fetchWikiScopes,
  reloadScenarios,
  setPlayerAssignment,
  setScenarioEnabled,
} from "@/lib/api";
import type { ModuleRow, PlayerAssignment, ScenarioRow } from "@/lib/config-pages";
import type { WikiScope } from "@/lib/wiki";
import { NewModuleDialog } from "@/components/modules/NewModuleDialog";

function enabledLabel(enabled: boolean | null): string {
  if (enabled === true) return "On";
  if (enabled === false) return "Off";
  return "Default";
}

function ModuleScenarios({
  moduleKey,
  scenarios,
  busyKey,
  onToggle,
}: {
  moduleKey: string;
  scenarios: ScenarioRow[];
  busyKey: string | null;
  onToggle: (row: ScenarioRow) => void;
}) {
  if (!scenarios.length) {
    return <p className="muted">No runnable scenarios in this module.</p>;
  }
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Key</th>
          <th>Name</th>
          <th>Steps</th>
          <th>Device</th>
          <th>Enabled</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {scenarios.map((s) => (
          <tr key={s.key}>
            <td>
              <code>{s.key}</code>
            </td>
            <td>{s.name}</td>
            <td>{s.steps}</td>
            <td>{s.device_level ? "yes" : "no"}</td>
            <td>
              <AppSwitch
                checked={s.enabled === true}
                disabled={busyKey === s.key}
                onChange={() => onToggle(s)}
                aria-label={`Enable scenario ${s.name}`}
                title={enabledLabel(s.enabled)}
              />
            </td>
            <td className="module-scenario-actions">
              <Link
                href={editDslHref({ module: moduleKey, scenario: s.path })}
                className="queue-task-actions__link"
              >
                Edit
              </Link>
              <Link
                href={debugRunHref({ scope: moduleKey, scenario: s.key })}
                className="queue-task-actions__link"
              >
                Run
              </Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ModulesPage() {
  const { showSuccess } = useFeedback();
  const [game, setGame] = useState<string>(GAME_TABS[0]?.id ?? "wos");
  const [scopes, setScopes] = useState<WikiScope[]>([]);
  const [scope, setScope] = useState("all");
  const [modules, setModules] = useState<ModuleRow[]>([]);
  const [players, setPlayers] = useState<PlayerAssignment[]>([]);
  const [assignmentsLoading, setAssignmentsLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);

  const allScenarios = useMemo(
    () => modules.flatMap((m) => m.scenarios),
    [modules],
  );

  const reload = useCallback(async () => {
    setError(null);
    setAssignmentsLoading(true);
    try {
      const [mods, p] = await Promise.all([
        fetchModules(scope, game),
        fetchPlayerAssignments(),
      ]);
      setModules(mods);
      setPlayers(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAssignmentsLoading(false);
    }
  }, [scope, game]);

  useEffect(() => {
    fetchWikiScopes(game).then(setScopes).catch(() => {});
  }, [game]);

  // Reset module scope when switching games — scopes are game-specific.
  useEffect(() => {
    setScope("all");
  }, [game]);

  useEffect(() => {
    reload();
  }, [reload]);

  const q = filter.trim().toLowerCase();
  const filtered = useMemo(() => {
    if (!q) return modules;
    return modules.filter(
      (m) =>
        m.id.toLowerCase().includes(q) ||
        m.title.toLowerCase().includes(q) ||
        m.storage_key.toLowerCase().includes(q) ||
        m.description.toLowerCase().includes(q) ||
        m.rel_path.toLowerCase().includes(q),
    );
  }, [modules, q]);

  function toggleExpanded(storageKey: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(storageKey)) next.delete(storageKey);
      else next.add(storageKey);
      return next;
    });
  }

  async function toggleScenario(row: ScenarioRow) {
    const next = row.enabled !== true;
    setBusy(row.key);
    try {
      await setScenarioEnabled(row.key, next);
      await reload();
      showSuccess(
        `${row.name}: ${next ? "enabled" : "disabled"} (${enabledLabel(next)})`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function onAssign(playerId: string, scenarioId: string) {
    setBusy(playerId);
    try {
      await setPlayerAssignment(playerId, scenarioId || null);
      await reload();
      showSuccess(
        scenarioId
          ? `Assigned ${scenarioId} to ${playerId}`
          : `Cleared scenario override for ${playerId}`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader title="Modules">
        <p className="muted">
          Module manifests under <code>modules/</code> — enable scenarios, assign
          per-player overrides, open editor or runner.
        </p>
      </PageHeader>
      <AppTabs
        tabs={GAME_TABS.map((g) => ({ key: g.id, label: g.label, title: g.id }))}
        selectedKey={game}
        onChange={setGame}
        renderPanels={false}
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <AppListbox
          inline
          label="Scope"
          value={scope}
          onChange={setScope}
          options={
            scopes.length
              ? scopes.map((s) => ({ value: s.key, label: s.label }))
              : [{ value: "all", label: "all" }]
          }
          minWidth={160}
        />
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter: id, title, path…"
          className="min-w-[12rem] flex-1 rounded-lg border border-wos-border-subtle bg-wos-input px-2.5 py-1.5 text-sm text-wos-text focus:border-sky-400/70 focus:outline-none focus:ring-2 focus:ring-sky-400/25"
          type="search"
        />
        <button type="button" className="btn-secondary" onClick={() => reload()}>
          Refresh
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={busy === "__reload__"}
          onClick={async () => {
            setBusy("__reload__");
            try {
              const loaded = await reloadScenarios();
              await reload();
              showSuccess(`Reloaded scenarios from disk (${loaded} loaded)`);
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(null);
            }
          }}
        >
          Reload from disk
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={() => setNewOpen(true)}
        >
          New module
        </button>
      </div>
      <NewModuleDialog
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={async (row) => {
          await reload();
          await reloadScenarios().catch(() => {});
          showSuccess(`Created module ${row.storage_key}`);
        }}
        onError={(message) => setError(message)}
      />
      <ErrorBanner message={error} />
      <section className="panel">
        <h2>
          Modules ({filtered.length})
        </h2>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th />
                <th>Module</th>
                <th>Path</th>
                <th>Scenarios</th>
                <th>Enabled</th>
                <th>Wiki</th>
                <th>Links</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((m) => {
                const open = expanded.has(m.storage_key);
                return (
                  <Fragment key={m.storage_key}>
                    <tr>
                      <td>
                        {m.scenario_count > 0 && (
                          <button
                            type="button"
                            className="btn-secondary"
                            aria-expanded={open}
                            onClick={() => toggleExpanded(m.storage_key)}
                          >
                            {open ? "−" : "+"}
                          </button>
                        )}
                      </td>
                      <td>
                        <div className="flex flex-wrap items-center gap-1.5">
                          <strong>{m.title}</strong>
                          {m.core ? (
                            <span className="rounded-full border border-sky-400/40 bg-sky-500/15 px-1.5 py-0 text-[10px] font-semibold uppercase tracking-wide text-sky-300">
                              core
                            </span>
                          ) : null}
                        </div>
                        <div className="muted text-xs">
                          <code>{m.storage_key}</code>
                        </div>
                        {m.description && (
                          <div className="muted text-xs">{m.description}</div>
                        )}
                      </td>
                      <td className="muted">
                        <code>{m.rel_path}</code>
                      </td>
                      <td>{m.scenario_count}</td>
                      <td>
                        <div className="flex flex-wrap items-center gap-1">
                          {m.enabled_on ? (
                            <span className="rounded-full bg-emerald-500/15 px-1.5 py-0 text-[10px] font-semibold uppercase tracking-wide text-emerald-300">
                              {m.enabled_on} on
                            </span>
                          ) : null}
                          {m.enabled_off ? (
                            <span className="rounded-full bg-wos-panel-raised px-1.5 py-0 text-[10px] font-semibold uppercase tracking-wide text-wos-text-muted">
                              {m.enabled_off} off
                            </span>
                          ) : null}
                          {!m.enabled_on && !m.enabled_off ? (
                            <span className="text-xs text-wos-text-muted">—</span>
                          ) : null}
                        </div>
                      </td>
                      <td>
                        {m.wiki ? (
                          <span className="status-pill status-idle">yes</span>
                        ) : (
                          <span className="text-xs text-wos-text-muted">—</span>
                        )}
                      </td>
                      <td>
                        <Link href={editDslHref({ module: m.storage_key })}>
                          DSL editor
                        </Link>
                        {" · "}
                        <Link
                          href={editDslHref({
                            module: m.storage_key,
                            newScenario: true,
                          })}
                        >
                          New scenario
                        </Link>
                        {" · "}
                        <Link
                          href={`/debug-run?scope=${encodeURIComponent(m.storage_key)}`}
                        >
                          Runner
                        </Link>
                        {" · "}
                        <Link href={`/analyze?scope=${encodeURIComponent(m.storage_key)}`}>
                          Analyze
                        </Link>
                      </td>
                    </tr>
                    {open && m.scenarios.length > 0 && (
                      <tr key={`${m.storage_key}-detail`}>
                        <td colSpan={7}>
                          <ModuleScenarios
                            moduleKey={m.storage_key}
                            scenarios={m.scenarios}
                            busyKey={busy}
                            onToggle={toggleScenario}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel panel--spaced">
        <h2>Player assignment</h2>
        <p className="muted">
          Redis override for account-level scenario routing (device-level scenarios
          ignore player).
        </p>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Player</th>
                <th>Assigned scenario</th>
              </tr>
            </thead>
            <tbody>
              {assignmentsLoading && players.length === 0 ? (
                <tr>
                  <td colSpan={2} className="meta">
                    Loading players…
                  </td>
                </tr>
              ) : null}
              {players.map((p) => (
                <tr key={p.player_id}>
                  <td>
                    <code>{p.player_id}</code>
                  </td>
                  <td>
                    <AppListbox
                      value={p.assigned_scenario ?? ""}
                      disabled={busy === p.player_id}
                      onChange={(v) => onAssign(p.player_id, v)}
                      options={[
                        { value: "", label: "(none)" },
                        ...allScenarios.map((s) => ({
                          value: s.key,
                          label: s.key,
                        })),
                      ]}
                      minWidth={220}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
