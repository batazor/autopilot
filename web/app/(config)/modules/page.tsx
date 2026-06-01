"use client";

import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { AppListbox, AppSwitch, AppTabs } from "@/components/headless";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { NewModuleDialog } from "@/components/modules/NewModuleDialog";
import { PageHeader } from "@/components/PageHeader";
import { Icon } from "@/components/ui/Icon";
import { Spinner } from "@/components/ui/Spinner";
import {
  fetchModules,
  fetchWikiScopes,
  reloadScenarios,
  setScenarioEnabled,
} from "@/lib/api";
import type { ModuleRow, ScenarioRow } from "@/lib/config-pages";
import { editDslHref } from "@/lib/debug-links";
import { filterModules } from "@/lib/modules-filter";
import type { WikiScope } from "@/lib/wiki";

const GAME_TABS: { id: string; label: string }[] = [
  { id: "wos", label: "Whiteout Survival" },
  { id: "kingshot", label: "Kingshot" },
];

function enabledLabel(enabled: boolean | null): string {
  if (enabled === true) return "On";
  if (enabled === false) return "Off";
  return "Default";
}

function scenarioCountLabel(count: number): string {
  return `${count} ${count === 1 ? "scenario" : "scenarios"}`;
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
  const enabledOn = scenarios.filter((s) => s.enabled === true).length;
  const enabledOff = scenarios.filter((s) => s.enabled === false).length;

  if (!scenarios.length) {
    return (
      <div className="module-scenario-panel">
        <div className="module-scenario-empty">
          <Icon name="list-empty" size="lg" />
          <span>No runnable scenarios in this module.</span>
        </div>
      </div>
    );
  }

  return (
    <div className="module-scenario-panel">
      <div className="module-scenario-panel__head">
        <div>
          <h3>Scenarios</h3>
          <p className="meta">
            {scenarioCountLabel(scenarios.length)} in this module
          </p>
        </div>
        <div className="module-scenario-panel__stats">
          <span className="status-pill status-idle">{enabledOn} on</span>
          {enabledOff ? (
            <span className="status-pill module-status-muted">{enabledOff} off</span>
          ) : null}
        </div>
      </div>
      <div className="data-table-wrap module-scenario-table-wrap">
        <table className="data-table module-scenario-table">
          <thead>
            <tr>
              <th>Key</th>
              <th>Name</th>
              <th>Steps</th>
              <th>Mode</th>
              <th>Enabled</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {scenarios.map((s) => (
              <tr
                key={s.key}
                className={
                  s.enabled === false
                    ? "module-scenario-row module-scenario-row--off"
                    : "module-scenario-row"
                }
              >
                <td>
                  <div className="module-scenario-key">
                    <code>{s.key}</code>
                    <span>{s.source}</span>
                  </div>
                </td>
                <td>
                  <div className="module-scenario-name">
                    <strong>{s.name}</strong>
                    <code>{s.path}</code>
                  </div>
                </td>
                <td>
                  <span className="module-count-pill">{s.steps}</span>
                </td>
                <td>
                  <span
                    className={`status-pill ${
                      s.device_level ? "module-status-device" : "module-status-muted"
                    }`}
                  >
                    {s.device_level ? "Device" : "General"}
                  </span>
                </td>
                <td>
                  <div className="module-scenario-enabled-cell">
                    <AppSwitch
                      checked={s.enabled === true}
                      disabled={busyKey === s.key}
                      onChange={() => onToggle(s)}
                      aria-label={`Enable scenario ${s.name}`}
                      title={enabledLabel(s.enabled)}
                    />
                    <span>{enabledLabel(s.enabled)}</span>
                  </div>
                </td>
                <td className="module-scenario-actions">
                  <Link
                    href={editDslHref({ module: moduleKey, scenario: s.path })}
                    className="module-action-link"
                    title={`Edit ${s.name}`}
                  >
                    <Icon name="edit-dsl" size="sm" />
                    Edit
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function ModulesPage() {
  const { showSuccess } = useFeedback();
  const [game, setGame] = useState<string>(GAME_TABS[0]?.id ?? "wos");
  const [scopes, setScopes] = useState<WikiScope[]>([]);
  const [scope, setScope] = useState("all");
  const [modules, setModules] = useState<ModuleRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);

  const reload = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const mods = await fetchModules(scope, game);
      setModules(mods);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [scope, game]);

  useEffect(() => {
    fetchWikiScopes(game).then(setScopes).catch(() => {});
  }, [game]);

  // Reset module scope when switching games because scopes are game-specific.
  useEffect(() => {
    setScope("all");
  }, [game]);

  useEffect(() => {
    reload();
  }, [reload]);

  const filtered = useMemo(() => filterModules(modules, filter), [modules, filter]);

  const scopeOptions = useMemo(() => {
    const mapped = scopes.map((s) => ({ value: s.key, label: s.label }));
    if (mapped.some((option) => option.value === "all")) {
      return mapped;
    }
    return [{ value: "all", label: "all" }, ...mapped];
  }, [scopes]);

  const selectedGameLabel =
    GAME_TABS.find((tab) => tab.id === game)?.label ?? game;
  const selectedScopeLabel =
    scopeOptions.find((option) => option.value === scope)?.label ?? scope;
  const visibleExpanded = filtered.filter((m) =>
    expanded.has(m.storage_key),
  ).length;

  const totals = useMemo(
    () =>
      filtered.reduce(
        (acc, moduleRow) => {
          acc.scenarios += moduleRow.scenario_count;
          acc.enabledOn += moduleRow.enabled_on;
          acc.enabledOff += moduleRow.enabled_off;
          acc.wiki += moduleRow.wiki ? 1 : 0;
          acc.analyzers += moduleRow.has_analyze ? 1 : 0;
          return acc;
        },
        { scenarios: 0, enabledOn: 0, enabledOff: 0, wiki: 0, analyzers: 0 },
      ),
    [filtered],
  );

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

  return (
    <>
      <PageHeader title="Modules">
        <div className="module-header">
          <p className="muted">
            Module manifests under <code>modules/</code> - enable scenarios and
            open the DSL editor.
          </p>
          <div className="module-header-metrics">
            <span className="module-metric">
              <span>Game</span>
              <strong>{selectedGameLabel}</strong>
            </span>
            <span className="module-metric">
              <span>Scope</span>
              <strong>{selectedScopeLabel}</strong>
            </span>
            <span className="module-metric">
              <span>Visible</span>
              <strong>
                {filtered.length}/{modules.length}
              </strong>
            </span>
            <span className="module-metric">
              <span>Scenarios</span>
              <strong>{totals.scenarios}</strong>
            </span>
            <span className="module-metric module-metric--wide">
              <span>Enabled</span>
              <strong>
                {totals.enabledOn} on / {totals.enabledOff} off
              </strong>
            </span>
            <span className="module-metric module-metric--wide">
              <span>Docs</span>
              <strong>
                {totals.wiki} wiki / {totals.analyzers} analyzers
              </strong>
            </span>
          </div>
        </div>
      </PageHeader>
      <AppTabs
        tabs={GAME_TABS.map((g) => ({ key: g.id, label: g.label, title: g.id }))}
        selectedKey={game}
        onChange={setGame}
        renderPanels={false}
      />
      <div className="module-toolbar">
        <AppListbox
          inline
          label="Scope"
          value={scope}
          onChange={setScope}
          options={scopeOptions}
          minWidth={160}
        />
        <label className="module-search">
          <Icon name="search" size="sm" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter id, title, path"
            type="search"
          />
          {filter ? (
            <button
              type="button"
              className="btn-icon module-search__clear"
              aria-label="Clear module filter"
              onClick={() => setFilter("")}
            >
              <Icon name="clear" size="sm" />
            </button>
          ) : null}
        </label>
        <button
          type="button"
          className="btn-secondary module-toolbar-button"
          onClick={() => void reload()}
        >
          <Icon name="refresh" size="sm" />
          Refresh
        </button>
        <button
          type="button"
          className="btn-secondary module-toolbar-button"
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
          <Icon name="recent" size="sm" />
          {busy === "__reload__" ? "Reloading" : "Reload disk"}
        </button>
        <button
          type="button"
          className="btn-primary module-toolbar-button"
          onClick={() => setNewOpen(true)}
        >
          <Icon name="modules" size="sm" />
          New module
        </button>
      </div>
      <NewModuleDialog
        open={newOpen}
        initialGame={game}
        onClose={() => setNewOpen(false)}
        onCreated={async (row) => {
          await reload();
          await reloadScenarios().catch(() => {});
          setExpanded((prev) => new Set(prev).add(row.storage_key));
          showSuccess(`Created module ${row.storage_key}`);
        }}
        onError={(message) => setError(message)}
      />
      <ErrorBanner
        message={error}
        onRetry={() => void reload()}
        retrying={loading}
      />
      <section className="panel module-editor-panel">
        <div className="module-panel-head">
          <div>
            <h2>Modules</h2>
            <p className="meta">
              {loading
                ? "Loading…"
                : error
                  ? "Couldn’t load modules"
                  : filtered.length === modules.length
                    ? `${modules.length} loaded`
                    : `${filtered.length} of ${modules.length} shown`}
            </p>
          </div>
          <span className="status-pill module-status-muted">
            {visibleExpanded} expanded
          </span>
        </div>
        {loading ? (
          <div className="module-empty">
            <Spinner />
            <span>Loading modules…</span>
          </div>
        ) : error ? null : filtered.length === 0 ? (
          <div className="module-empty">
            <Icon name="list-empty" size="lg" />
            <strong>
              {modules.length === 0
                ? "No modules found"
                : "No modules match this filter"}
            </strong>
            <span>
              {modules.length === 0
                ? "Nothing is registered for this game and scope yet."
                : "Clear the filter or switch scope to see more modules."}
            </span>
            {filter ? (
              <button
                type="button"
                className="btn-secondary module-toolbar-button"
                onClick={() => setFilter("")}
              >
                <Icon name="clear" size="sm" />
                Clear filter
              </button>
            ) : null}
          </div>
        ) : (
          <div className="data-table-wrap module-table-wrap">
            <table className="data-table module-table">
              <thead>
                <tr>
                  <th />
                  <th>Module</th>
                  <th>Path</th>
                  <th>Scenarios</th>
                  <th>Enabled</th>
                  <th>Docs</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((m) => {
                  const open = expanded.has(m.storage_key);
                  const hasEnabledState = m.enabled_on > 0 || m.enabled_off > 0;
                  return (
                    <Fragment key={m.storage_key}>
                      <tr
                        className={[
                          "module-row",
                          open ? "module-row--open" : "",
                          m.scenario_count === 0 ? "module-row--empty" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                      >
                        <td className="module-table__expander">
                          {m.scenario_count > 0 ? (
                            <button
                              type="button"
                              className="btn-icon module-expand-button"
                              aria-expanded={open}
                              aria-label={`${open ? "Hide" : "Show"} scenarios for ${
                                m.title
                              }`}
                              onClick={() => toggleExpanded(m.storage_key)}
                            >
                              <Icon
                                name="chevron-right"
                                size="sm"
                                className={
                                  open
                                    ? "module-expand-icon module-expand-icon--open"
                                    : "module-expand-icon"
                                }
                              />
                            </button>
                          ) : (
                            <span className="module-expand-placeholder">
                              <Icon name="dot" size="sm" />
                            </span>
                          )}
                        </td>
                        <td>
                          <div className="module-title-cell">
                            <div className="module-title-line">
                              <strong>{m.title}</strong>
                              <span
                                className={`module-kind-pill ${
                                  m.core
                                    ? "module-kind-pill--core"
                                    : "module-kind-pill--custom"
                                }`}
                              >
                                {m.core ? "Core" : "Custom"}
                              </span>
                            </div>
                            <code>{m.storage_key}</code>
                            {m.description ? <p>{m.description}</p> : null}
                          </div>
                        </td>
                        <td className="module-path-cell">
                          <code>{m.rel_path}</code>
                        </td>
                        <td>
                          <div className="module-scenario-summary">
                            <strong>{m.scenario_count}</strong>
                            <span>
                              {m.scenario_count === 1
                                ? "scenario"
                                : "scenarios"}
                            </span>
                          </div>
                        </td>
                        <td>
                          <div className="module-enabled-stack">
                            {m.enabled_on ? (
                              <span className="status-pill status-idle">
                                {m.enabled_on} on
                              </span>
                            ) : null}
                            {m.enabled_off ? (
                              <span className="status-pill module-status-muted">
                                {m.enabled_off} off
                              </span>
                            ) : null}
                            {!hasEnabledState ? (
                              <span className="status-pill module-status-muted">
                                Default
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td>
                          <div className="module-docs-stack">
                            <span
                              className={`status-pill ${
                                m.wiki
                                  ? "module-status-wiki"
                                  : "module-status-muted"
                              }`}
                            >
                              <Icon name="wiki" size="sm" />
                              {m.wiki ? "Wiki" : "No wiki"}
                            </span>
                            <span
                              className={`status-pill ${
                                m.has_analyze
                                  ? "module-status-analyze"
                                  : "module-status-muted"
                              }`}
                            >
                              <Icon name="optimizer" size="sm" />
                              {m.has_analyze ? "Analyzer" : "No analyzer"}
                            </span>
                          </div>
                        </td>
                        <td>
                          <div className="module-row-actions">
                            <Link
                              href={editDslHref({ module: m.storage_key })}
                              className="module-action-link"
                            >
                              <Icon name="edit-dsl" size="sm" />
                              DSL
                            </Link>
                            <Link
                              href={editDslHref({
                                module: m.storage_key,
                                newScenario: true,
                              })}
                              className="module-action-link"
                            >
                              <Icon name="plus" size="sm" />
                              Scenario
                            </Link>
                            <Link
                              href={`/analyze?scope=${encodeURIComponent(m.storage_key)}`}
                              className="module-action-link"
                            >
                              <Icon name="optimizer" size="sm" />
                              Analyze
                            </Link>
                          </div>
                        </td>
                      </tr>
                      {open && m.scenarios.length > 0 && (
                        <tr
                          key={`${m.storage_key}-detail`}
                          className="module-detail-row"
                        >
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
        )}
      </section>
    </>
  );
}
