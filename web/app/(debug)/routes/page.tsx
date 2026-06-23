"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CopyButton } from "@/components/CopyButton";
import { AppListbox, AppTabs } from "@/components/headless";
import { PageHeader } from "@/components/PageHeader";
import { MetricCard, MetricGrid } from "@/components/ui";
import { SearchField } from "@/components/player-state/SearchField";
import {
  fetchLabelingReferences,
  fetchRoutesEdges,
  fetchRoutesGraph,
  fetchRoutesNode,
  fetchRoutesScreenZones,
  labelingImageUrl,
} from "@/lib/api";
import type {
  LabelingReferenceMeta,
  RoutesGraphResponse,
  RoutesGraphView,
  RoutesNodeDetails,
  RoutesScreenZones,
  RoutesZone,
} from "@/lib/types";

function RoutesReferencePanel({
  path,
  selectedScreen,
  refByScreen,
}: {
  path: string[] | null;
  selectedScreen: string | null;
  refByScreen: Map<string, LabelingReferenceMeta>;
}) {
  const screens = path && path.length > 0 ? path : selectedScreen ? [selectedScreen] : [];
  if (!screens.length) {
    return (
      <aside className="panel">
        <h2 className="m-0 mb-2 text-base font-semibold">References</h2>
        <p className="muted m-0">
          Plan a route or pick a screen to preview its reference screenshots.
        </p>
      </aside>
    );
  }
  return (
    <aside className="panel">
      <h2 className="m-0 mb-3 text-base font-semibold">
        References{" "}
        {path && path.length > 0 ? (
          <span className="muted text-sm font-normal">
            · {screens.length} step{screens.length === 1 ? "" : "s"}
          </span>
        ) : null}
      </h2>
      <ol className="routes-ref-list">
        {screens.map((s, i) => {
          const ref = refByScreen.get(s);
          return (
            <li key={`${s}-${i}`} className="routes-ref-list__item">
              <div className="routes-ref-list__head">
                <span className="routes-ref-list__step">{i + 1}</span>
                <code className="routes-ref-list__screen">{s}</code>
                {ref ? (
                  <span className="routes-ref-list__count">
                    {ref.region_count} region{ref.region_count === 1 ? "" : "s"}
                  </span>
                ) : null}
              </div>
              {ref ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={labelingImageUrl(ref.rel)}
                  alt={`Reference for ${s}`}
                  className="routes-ref-list__image"
                  loading="lazy"
                />
              ) : (
                <p className="routes-ref-list__empty">
                  No labeled reference for this screen.
                </p>
              )}
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

function zoneKey(z: RoutesZone): string {
  return `${z.kind}:${z.region}:${z.to ?? ""}`;
}

/** Reference screenshot with clickable transition/region zones drawn on top.
 *  Zone bbox is percent-of-reference, so absolute % positioning scales with the
 *  image at any width — no canvas math needed. */
function RoutesZoneMap({
  imageUrl,
  zones,
  showRegions,
  selectedKey,
  onSelect,
}: {
  imageUrl: string | null;
  zones: RoutesZone[];
  showRegions: boolean;
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  if (!imageUrl) {
    return (
      <div className="routes-zonemap routes-zonemap--empty">
        No labeled reference for this screen — capture one in Labeling first.
      </div>
    );
  }
  return (
    <div className="routes-zonemap">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={imageUrl} alt="Reference screenshot" className="routes-zonemap__img" />
      {zones.map((z) => {
        if (z.kind === "region" && !showRegions) return null;
        const key = zoneKey(z);
        const sel = selectedKey === key;
        const title =
          z.kind === "transition"
            ? `→ ${z.to} · tap ${z.region}`
            : `${z.region}${z.action ? ` · ${z.action}` : ""}`;
        return (
          <button
            key={key}
            type="button"
            className={`routes-zone routes-zone--${z.kind}${sel ? " is-selected" : ""}`}
            style={{
              left: `${z.bbox.x}%`,
              top: `${z.bbox.y}%`,
              width: `${z.bbox.width}%`,
              height: `${z.bbox.height}%`,
            }}
            title={title}
            onClick={() => onSelect(key)}
          >
            <span className="routes-zone__label">
              {z.kind === "transition" ? `→ ${z.to}` : z.region}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function graphViewOptions(total: number): { value: RoutesGraphView; label: string }[] {
  return [
    { value: "hub", label: "Hub tree (2 levels)" },
    { value: "focus", label: "Focus subtree" },
    { value: "path", label: "Planned route only" },
    { value: "full", label: `Full graph (${total} screens)` },
  ];
}

export default function RoutesPage() {
  const [tab, setTab] = useState<"planner" | "edges" | "zones">("planner");
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

  const [zoneScreen, setZoneScreen] = useState("main_city");
  const [zoneData, setZoneData] = useState<RoutesScreenZones | null>(null);
  const [zoneSel, setZoneSel] = useState<string | null>(null);
  const [showRegions, setShowRegions] = useState(true);

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

  useEffect(() => {
    if (tab !== "zones" || !zoneScreen) return;
    setZoneSel(null);
    setZoneData(null);
    fetchRoutesScreenZones(zoneScreen)
      .then((d) => {
        setZoneData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [tab, zoneScreen]);

  const planTo = useCallback((dst: string) => {
    setFrom(zoneScreen);
    setTo(dst);
    setGraphView("path");
    setTab("planner");
  }, [zoneScreen]);

  const [refs, setRefs] = useState<LabelingReferenceMeta[]>([]);
  useEffect(() => {
    fetchLabelingReferences("all")
      .then(setRefs)
      .catch(() => setRefs([]));
  }, []);

  const refByScreen = useMemo(() => {
    const m = new Map<string, LabelingReferenceMeta>();
    for (const r of refs) {
      if (r.screen_id && !m.has(r.screen_id)) m.set(r.screen_id, r);
    }
    return m;
  }, [refs]);

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

  const zoneScreenOptions = useMemo(() => {
    const set = new Set<string>(graph?.screens ?? []);
    for (const k of refByScreen.keys()) set.add(k);
    return Array.from(set)
      .sort()
      .map((s) => ({ value: s, label: s }));
  }, [graph?.screens, refByScreen]);

  const zoneRef = refByScreen.get(zoneScreen) ?? null;
  const zoneImg = zoneRef ? labelingImageUrl(zoneRef.rel) : null;
  const selectedZone = useMemo(
    () => zoneData?.zones.find((z) => zoneKey(z) === zoneSel) ?? null,
    [zoneData, zoneSel],
  );

  return (
    <>
      <PageHeader title="Screen routes" fleet>
        <p className="muted">
          Screen tree from <code>main_city</code> — plan paths and inspect transitions
          from the navigation graph (<code>area.json</code> / screen graph).
        </p>
      </PageHeader>
      {error ? <div className="error-banner">{error}</div> : null}

      <div className="page-stack">
      {m ? (
        <MetricGrid>
          <MetricCard label="Tree edges" value={m.tree_edges} />
          <MetricCard label="All transitions" value={m.page_transitions} />
          <MetricCard label="Registered taps" value={m.static_edges} />
          <MetricCard
            label="In view"
            value={
              <>
                {visibleCount}
                <span className="meta" style={{ fontSize: "0.75rem" }}>
                  {" "}
                  / {totalScreens}
                </span>
              </>
            }
          />
        </MetricGrid>
      ) : null}

      <AppTabs
        tabs={[
          { key: "planner", label: "Route planner" },
          { key: "edges", label: "All edges" },
          { key: "zones", label: "Transition map" },
        ]}
        selectedKey={tab}
        onChange={(key) => {
          if (key === "zones" && selectedNode) setZoneScreen(selectedNode);
          setTab(key as "planner" | "edges" | "zones");
        }}
        renderPanels={false}
      />

      {tab === "planner" ? (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
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
        <RoutesReferencePanel
          path={graph?.path ?? null}
          selectedScreen={selectedNode}
          refByScreen={refByScreen}
        />
        </div>
      ) : tab === "edges" ? (
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
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
          <section className="panel routes-zones-panel">
            <div className="routes-zones-toolbar">
              <AppListbox
                fullWidth
                label="Screen"
                value={zoneScreen}
                onChange={setZoneScreen}
                options={zoneScreenOptions}
                placeholder="Pick a screen…"
              />
              <label className="routes-zones-toggle">
                <input
                  type="checkbox"
                  checked={showRegions}
                  onChange={(e) => setShowRegions(e.target.checked)}
                />
                <span>Show other labeled regions</span>
              </label>
            </div>
            <p className="meta routes-zones-help">
              Transition tap-zones are drawn over the real reference screenshot.
              Cyan zones lead somewhere (an edge in the graph); amber zones are
              labeled regions with <strong>no transition yet</strong>. Bare areas
              have no markup at all — candidates for a missing route.
            </p>
            <RoutesZoneMap
              imageUrl={zoneImg}
              zones={zoneData?.zones ?? []}
              showRegions={showRegions}
              selectedKey={zoneSel}
              onSelect={setZoneSel}
            />
          </section>

          <aside className="panel routes-zones-side">
            {zoneData ? (
              <>
                <div className="routes-zones-legend">
                  <span className="routes-zones-legend__item routes-zones-legend__item--transition">
                    {zoneData.counts.transitions} transition
                    {zoneData.counts.transitions === 1 ? "" : "s"}
                  </span>
                  <span className="routes-zones-legend__item routes-zones-legend__item--region">
                    {zoneData.counts.regions} other region
                    {zoneData.counts.regions === 1 ? "" : "s"}
                  </span>
                  {zoneData.counts.unmapped > 0 ? (
                    <span className="routes-zones-legend__item routes-zones-legend__item--unmapped">
                      {zoneData.counts.unmapped} dynamic/unmapped
                    </span>
                  ) : null}
                </div>

                {selectedZone ? (
                  <div className="routes-zone-detail">
                    <code className="routes-zone-detail__region">
                      {selectedZone.region}
                    </code>
                    {selectedZone.kind === "transition" ? (
                      <>
                        <p className="meta">
                          Leads to <code>{selectedZone.to}</code> ·{" "}
                          {selectedZone.status}
                        </p>
                        <button
                          type="button"
                          className="btn-secondary routes-zone-detail__btn"
                          onClick={() => selectedZone.to && planTo(selectedZone.to)}
                        >
                          Plan route {zoneScreen} → {selectedZone.to}
                        </button>
                      </>
                    ) : (
                      <p className="meta">
                        Labeled region (<code>{selectedZone.action || "—"}</code>)
                        with no transition bound to it.
                        {selectedZone.has_red_dot ? " · red-dot" : ""}
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="meta">
                    Click a zone on the screenshot to inspect it.
                  </p>
                )}

                <h3 className="routes-panel-subhead">
                  Transitions from <code>{zoneScreen}</code>
                </h3>
                {zoneData.zones.some((z) => z.kind === "transition") ? (
                  <ul className="routes-zone-list">
                    {zoneData.zones
                      .filter((z) => z.kind === "transition")
                      .map((z) => {
                        const key = zoneKey(z);
                        return (
                          <li key={key}>
                            <button
                              type="button"
                              className={`routes-zone-list__btn${
                                zoneSel === key ? " is-selected" : ""
                              }`}
                              onClick={() => setZoneSel(key)}
                            >
                              <span className="routes-zone-list__to">→ {z.to}</span>
                              <code className="routes-zone-list__region">
                                {z.region}
                              </code>
                            </button>
                          </li>
                        );
                      })}
                  </ul>
                ) : (
                  <p className="meta">No mapped transitions from this screen.</p>
                )}

                {zoneData.unmapped.length > 0 ? (
                  <>
                    <h3 className="routes-panel-subhead">Dynamic / unmapped</h3>
                    <ul className="routes-zone-list routes-zone-list--muted">
                      {zoneData.unmapped.map((u, i) => (
                        <li key={`${u.to}-${u.region}-${i}`}>
                          <span className="routes-zone-list__to">→ {u.to}</span>
                          <code className="routes-zone-list__region">
                            {u.region}
                          </code>
                          <span className="routes-zone-list__status">{u.status}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </>
            ) : (
              <p className="muted m-0">Pick a screen to map its transition zones.</p>
            )}
          </aside>
        </div>
      )}
      </div>
    </>
  );
}
