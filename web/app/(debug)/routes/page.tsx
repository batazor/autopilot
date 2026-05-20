"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CopyButton } from "@/components/CopyButton";
import { AppListbox, AppTabs } from "@/components/headless";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { SearchField } from "@/components/player-state/SearchField";
import {
  fetchRoutesEdges,
  fetchRoutesGraph,
  fetchRoutesNode,
} from "@/lib/api";
import type {
  RoutesGraphResponse,
  RoutesGraphView,
  RoutesNodeDetails,
} from "@/lib/types";

function graphViewOptions(total: number): { value: RoutesGraphView; label: string }[] {
  return [
    { value: "hub", label: "Hub tree (2 levels)" },
    { value: "focus", label: "Focus subtree" },
    { value: "path", label: "Planned route only" },
    { value: "full", label: `Full graph (${total} screens)` },
  ];
}

export default function RoutesPage() {
  const [tab, setTab] = useState<"planner" | "edges">("planner");
  const [graphView, setGraphView] = useState<RoutesGraphView>("hub");
  const [hubDepth, setHubDepth] = useState(2);
  const [nodeSearch, setNodeSearch] = useState("");
  const [graph, setGraph] = useState<RoutesGraphResponse | null>(null);
  const [from, setFrom] = useState("main_city");
  const [to, setTo] = useState("");
  const [focus, setFocus] = useState("");
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [nodeDetail, setNodeDetail] = useState<RoutesNodeDetails | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [edgeFilter, setEdgeFilter] = useState("");
  const [edgeRows, setEdgeRows] = useState<
    Array<{ from: string; to: string; status: string; action: string }>
  >([]);
  const [edgeMeta, setEdgeMeta] = useState({ total: 0, shown: 0 });

  const loadGraph = useCallback(async () => {
    try {
      const data = await fetchRoutesGraph({
        from: from || undefined,
        to: to || undefined,
        focus: focus || undefined,
        view: graphView,
        hub_depth: hubDepth,
      });
      setGraph(data);
      if (!from && data.screens.includes("main_city")) setFrom("main_city");
      else if (!from && data.screens.length) setFrom(data.screens[0]);
      if (!to && data.screens.length) {
        const pick = data.screens.find((s) => s !== (from || "main_city"));
        if (pick) setTo(pick);
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [from, to, focus, graphView, hubDepth]);

  useEffect(() => {
    if (tab === "planner") loadGraph();
  }, [tab, loadGraph]);

  useEffect(() => {
    if (!selectedNode) {
      setNodeDetail(null);
      return;
    }
    fetchRoutesNode(selectedNode)
      .then(setNodeDetail)
      .catch((e: Error) => setError(e.message));
  }, [selectedNode]);

  const loadEdges = useCallback(async () => {
    try {
      const data = await fetchRoutesEdges(edgeFilter);
      setEdgeRows(data.edges as typeof edgeRows);
      setEdgeMeta({ total: data.total, shown: data.shown });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [edgeFilter]);

  useEffect(() => {
    if (tab === "edges") loadEdges();
  }, [tab, loadEdges]);

  const m = graph?.metrics;
  const visibleCount = graph?.visible_count ?? graph?.screens.length ?? 0;
  const totalScreens = graph?.total_screens ?? graph?.screens.length ?? 0;

  const screenOptions = (graph?.screens ?? []).map((s) => ({ value: s, label: s }));

  const nodePickerOptions = useMemo(() => {
    const q = nodeSearch.trim().toLowerCase();
    const screens = graph?.screens ?? [];
    const filtered = q
      ? screens.filter((s) => s.toLowerCase().includes(q))
      : screens;
    return filtered.map((s) => ({ value: s, label: s }));
  }, [graph?.screens, nodeSearch]);

  const routeClipboard = graph?.path?.length ? graph.path.join(",") : "";

  return (
    <>
      <FleetPageHeader title="Screen routes" />
      <p className="meta">
        Screen tree from <code>main_city</code> — plan paths and inspect transitions
        from the navigation graph (<code>area.json</code> / screen graph).
      </p>
      {error ? <div className="error-banner">{error}</div> : null}

      {m ? (
        <div className="metrics-row">
          <div className="metric-card">
            <div className="label">Tree edges</div>
            <div className="value">{m.tree_edges}</div>
          </div>
          <div className="metric-card">
            <div className="label">All transitions</div>
            <div className="value">{m.page_transitions}</div>
          </div>
          <div className="metric-card">
            <div className="label">Registered taps</div>
            <div className="value">{m.static_edges}</div>
          </div>
          <div className="metric-card">
            <div className="label">In view</div>
            <div className="value">
              {visibleCount}
              <span className="meta" style={{ fontSize: "0.75rem" }}>
                {" "}
                / {totalScreens}
              </span>
            </div>
          </div>
        </div>
      ) : null}

      <AppTabs
        tabs={[
          { key: "planner", label: "Route planner" },
          { key: "edges", label: "All edges" },
        ]}
        selectedKey={tab}
        onChange={(key) => setTab(key as "planner" | "edges")}
        renderPanels={false}
      />

      {tab === "planner" ? (
        <section className="panel routes-planner-panel">
          <h2>Route planner</h2>
          <AppListbox
            fullWidth
            label="Graph view"
            value={graphView}
            onChange={(v) => setGraphView(v as RoutesGraphView)}
            options={graphViewOptions(totalScreens).map((o) => ({
              value: o.value,
              label: o.label,
            }))}
          />
          {graphView === "hub" ? (
            <label className="routes-depth-field">
              <span className="meta">Depth from hub</span>
              <input
                type="number"
                min={1}
                max={6}
                value={hubDepth}
                onChange={(e) =>
                  setHubDepth(Math.max(1, Math.min(6, Number(e.target.value) || 2)))
                }
              />
            </label>
          ) : null}
          <SearchField
            label="Find screen"
            value={nodeSearch}
            onChange={setNodeSearch}
            placeholder="screen id…"
            className="routes-node-search"
          />
          <AppListbox
            fullWidth
            className="mt-2"
            label="From"
            value={from}
            onChange={setFrom}
            options={screenOptions}
          />
          <AppListbox
            fullWidth
            className="mt-2"
            label="To"
            value={to}
            onChange={setTo}
            options={screenOptions}
          />
          <AppListbox
            fullWidth
            className="mt-2"
            label="Focus node"
            value={focus}
            onChange={setFocus}
            placeholder="—"
            options={[{ value: "", label: "—" }, ...screenOptions]}
          />
          <button
            type="button"
            className="btn-primary routes-plan-btn"
            onClick={() => {
              if (from && to) setGraphView("path");
              void loadGraph();
            }}
          >
            {from && to ? "Plan route" : "Apply view"}
          </button>

          {graph?.path ? (
            <div className="routes-path-summary">
              {graph.mode === "via_main_city" && from !== to ? (
                <p className="meta">Route via <code>main_city</code></p>
              ) : null}
              <div className="routes-path-summary__head">
                <p className="meta">
                  <strong>Path</strong> ({graph.path.length} screens)
                </p>
                <CopyButton
                  text={routeClipboard}
                  label="Copy route"
                  title="Copy path as CSV (same as approval_path in navigation approvals)"
                />
              </div>
              <code className="routes-path-code">{graph.path.join(" → ")}</code>
              {graph.hops.length > 0 ? (
                <ul className="meta routes-hop-list">
                  {graph.hops.map((h) => (
                    <li key={h.n}>
                      {h.hop} · {h.status}
                      {h.action ? ` · ${h.action}` : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : graph && from && to ? (
            <p className="meta routes-path-miss">
              No route found (including via main_city).
            </p>
          ) : null}

          <h3 className="routes-panel-subhead">Screen detail</h3>
          <AppListbox
            fullWidth
            label="Screen"
            value={selectedNode ?? ""}
            onChange={(v) => setSelectedNode(v || null)}
            placeholder={graph ? "Select a screen…" : "Load graph first"}
            options={nodePickerOptions}
            disabled={!graph}
          />
          {nodeDetail ? (
            <>
              <p className="meta">
                <code>{nodeDetail.node_id}</code> — in {nodeDetail.incoming} / out{" "}
                {nodeDetail.outgoing}
              </p>
              <div className="data-table-wrap routes-node-edges-table">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>dir</th>
                      <th>edge</th>
                      <th>status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {nodeDetail.edges.map((row) => (
                      <tr key={`${row.dir}-${row.edge}`}>
                        <td>{row.dir}</td>
                        <td>
                          <code className="routes-edge-code">{row.edge}</code>
                        </td>
                        <td>{row.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <p className="meta">Pick a screen to see incoming/outgoing edges.</p>
          )}
        </section>
      ) : (
        <section className="panel">
          <div className="toolbar">
            <input
              type="search"
              placeholder="Filter edges…"
              value={edgeFilter}
              onChange={(e) => setEdgeFilter(e.target.value)}
            />
            <button type="button" className="btn-secondary" onClick={loadEdges}>
              Apply
            </button>
          </div>
          <p className="meta">
            Showing {edgeMeta.shown} of {edgeMeta.total} edges
          </p>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>from</th>
                  <th>to</th>
                  <th>status</th>
                  <th>action</th>
                </tr>
              </thead>
              <tbody>
                {edgeRows.map((r) => (
                  <tr key={`${r.from}-${r.to}`}>
                    <td>
                      <code>{r.from}</code>
                    </td>
                    <td>
                      <code>{r.to}</code>
                    </td>
                    <td>{r.status}</td>
                    <td className="routes-edge-action">{r.action || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
