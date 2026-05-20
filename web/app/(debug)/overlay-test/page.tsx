"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApprovalCanvas } from "@/components/ApprovalCanvas";
import { AppCheckbox, AppListbox } from "@/components/headless";
import { useFleet } from "@/components/FleetContextProvider";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { fetchOverlayTest, overlayTestImageUrl } from "@/lib/api";
import { usePollWhenVisible, useStableCacheKey } from "@/lib/hooks";
import {
  defaultActionVisibility,
  overlayLabelRuleName,
  OVERLAY_ACTION_TYPES,
  type MatchStatusFilter,
} from "@/lib/overlay-test";
import type { OverlayRuleRow, OverlayShape, OverlayTestResult } from "@/lib/types";

const POLL_MS = 1500;

function filterRules(
  rules: OverlayRuleRow[],
  opts: {
    text: string;
    matchStatus: MatchStatusFilter;
    nodeFilter: string;
    actionsVisible: Record<string, boolean>;
    onlyCurrentNode: boolean;
    currentScreen: string;
  },
): OverlayRuleRow[] {
  const needle = opts.text.trim().toLowerCase();
  return rules.filter((r) => {
    if (opts.matchStatus === "matched" && !r.matched) return false;
    if (opts.matchStatus === "unmatched" && r.matched) return false;

    const act = (r.action || "").trim();
    if (act && OVERLAY_ACTION_TYPES.includes(act as (typeof OVERLAY_ACTION_TYPES)[number])) {
      if (!opts.actionsVisible[act]) return false;
    }

    if (opts.nodeFilter) {
      if (opts.nodeFilter === "__global__") {
        if (r.node) return false;
      } else if (r.node !== opts.nodeFilter) {
        return false;
      }
    }

    if (opts.onlyCurrentNode && opts.currentScreen) {
      const node = (r.node || "").trim();
      if (node && node.toLowerCase() !== opts.currentScreen.toLowerCase()) {
        return false;
      }
    }

    if (!needle) return true;
    const hay = `${r.name} ${r.region} ${r.search_region} ${r.node} ${r.action} ${r.notes}`.toLowerCase();
    return hay.includes(needle);
  });
}

function filterCanvasOverlays(
  overlays: OverlayShape[],
  visibleRules: OverlayRuleRow[],
  highlightRule: string | null,
): OverlayShape[] {
  const visibleNames = new Set(visibleRules.map((r) => r.name));
  const visibleSearch = new Set(
    visibleRules.map((r) => r.search_region).filter((s) => s.trim()),
  );
  const highlight = highlightRule
    ? visibleRules.find((r) => r.name === highlightRule)
    : null;

  return overlays.filter((o) => {
    if (o.type === "crosshair") {
      if (highlightRule) return true;
      return visibleNames.size > 0;
    }
    if (o.type !== "rect" || !o.label) return true;

    const label = o.label;
    if (label.startsWith("search:")) {
      const sr = label.slice("search:".length);
      if (highlight) return highlight.search_region === sr;
      return visibleSearch.has(sr);
    }

    const ruleName = overlayLabelRuleName(label);
    if (!ruleName) return true;
    if (highlightRule) return ruleName === highlightRule;
    return visibleNames.has(ruleName);
  });
}

export default function OverlayTestPage() {
  const searchParams = useSearchParams();
  const regionParam =
    searchParams.get("region") ?? searchParams.get("highlight");
  const { instanceId, instancesError } = useFleet();
  const [result, setResult] = useState<OverlayTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const [onlyCurrentScreen, setOnlyCurrentScreen] = useState(false);
  const [ignoreScreenGate, setIgnoreScreenGate] = useState(false);

  const [textFilter, setTextFilter] = useState("");
  const [matchStatus, setMatchStatus] = useState<MatchStatusFilter>("all");
  const [onlyCurrentNode, setOnlyCurrentNode] = useState(true);
  const [nodeFilter, setNodeFilter] = useState("");
  const [actionsVisible, setActionsVisible] = useState(defaultActionVisibility);
  const [highlightRule, setHighlightRule] = useState<string | null>(null);

  useEffect(() => {
    if (!regionParam?.trim()) return;
    const r = regionParam.trim();
    setTextFilter(r);
    setHighlightRule(r);
    setOnlyCurrentNode(false);
  }, [regionParam]);

  const refresh = useCallback(async () => {
    if (!instanceId) return;
    try {
      const data = await fetchOverlayTest(instanceId, {
        onlyCurrentScreen: onlyCurrentScreen && !ignoreScreenGate,
        ignoreScreenGate,
      });
      setResult(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [instanceId, onlyCurrentScreen, ignoreScreenGate]);

  usePollWhenVisible(refresh, POLL_MS, autoRefresh);

  useEffect(() => {
    if (!regionParam?.trim() || !result?.rules.length) return;
    const needle = regionParam.trim();
    const hit = result.rules.find(
      (rule) =>
        rule.name === needle ||
        rule.region === needle ||
        rule.search_region === needle,
    );
    if (hit) setHighlightRule(hit.name);
  }, [regionParam, result?.rules]);

  const nodeOptions = useMemo(() => {
    const empty = { sorted: [] as string[], hasGlobal: false };
    if (!result) return empty;
    const nodes = new Set<string>();
    let hasGlobal = false;
    for (const r of result.rules) {
      const n = (r.node || "").trim();
      if (n) nodes.add(n);
      else hasGlobal = true;
    }
    return { sorted: [...nodes].sort(), hasGlobal };
  }, [result]);

  const filteredRules = useMemo(() => {
    if (!result) return [];
    return filterRules(result.rules, {
      text: textFilter,
      matchStatus,
      nodeFilter,
      actionsVisible,
      onlyCurrentNode: onlyCurrentNode && !ignoreScreenGate,
      currentScreen: result.current_screen || "",
    });
  }, [
    result,
    textFilter,
    matchStatus,
    nodeFilter,
    actionsVisible,
    onlyCurrentNode,
    ignoreScreenGate,
  ]);

  const sortedRules = useMemo(
    () =>
      [...filteredRules].sort((a, b) => {
        if (a.matched !== b.matched) return a.matched ? -1 : 1;
        return a.name.localeCompare(b.name);
      }),
    [filteredRules],
  );

  const canvasOverlays = useMemo(
    () => filterCanvasOverlays(result?.overlays ?? [], sortedRules, highlightRule),
    [result?.overlays, sortedRules, highlightRule],
  );

  const previewCacheKey = useStableCacheKey(
    result?.preview.available ? (result.preview.mtime ?? "pending") : null,
  );

  const imageUrl =
    result?.preview.available && instanceId
      ? overlayTestImageUrl(instanceId, previewCacheKey)
      : null;

  const clearDisplayFilters = () => {
    setTextFilter("");
    setMatchStatus("all");
    setNodeFilter("");
    setOnlyCurrentNode(true);
    setActionsVisible(defaultActionVisibility());
    setHighlightRule(null);
  };

  const toggleAction = (action: string) => {
    setActionsVisible((prev) => ({ ...prev, [action]: !prev[action] }));
  };

  return (
    <>
      <FleetPageHeader title="Overlay test">
        {result ? (
          <span className="status-pill status-idle" title="Matched / rules in this response">
            {result.matched_count} / {result.total_rules} matched
            {sortedRules.length !== result.total_rules
              ? ` · ${sortedRules.length} shown`
              : null}
          </span>
        ) : null}
      </FleetPageHeader>
      {error || instancesError ? (
        <div className="error-banner">{error ?? instancesError}</div>
      ) : null}

      <div className="toolbar">
        <AppCheckbox
          inline
          checked={autoRefresh}
          onChange={setAutoRefresh}
          label="Auto-refresh"
        />
        <button
          type="button"
          className="btn-secondary"
          onClick={refresh}
          title="Re-run overlay analysis on the latest frame"
        >
          Refresh now
        </button>
        {result ? (
          <span className="meta">
            screen: <code>{result.current_screen || "—"}</code>
            {result.active_player ? (
              <>
                {" "}
                · player: <code>{result.active_player}</code>
              </>
            ) : null}
          </span>
        ) : null}
      </div>

      <section className="panel" style={{ marginBottom: "1rem" }}>
        <h2>Filters</h2>

        <div className="toolbar" style={{ marginBottom: "0.75rem" }}>
          <label>
            Search
            <input
              type="search"
              value={textFilter}
              onChange={(e) => setTextFilter(e.target.value)}
              placeholder="rule, region, search_region, action…"
            />
          </label>
          <AppListbox
            inline
            label="Match"
            value={matchStatus}
            onChange={(v) => setMatchStatus(v as MatchStatusFilter)}
            options={[
              { value: "all", label: "All" },
              { value: "matched", label: "Matched only" },
              { value: "unmatched", label: "Unmatched only" },
            ]}
            minWidth={160}
          />
          <AppListbox
            inline
            label="Node"
            value={nodeFilter}
            onChange={setNodeFilter}
            options={[
              { value: "", label: "All nodes" },
              ...(nodeOptions.hasGlobal
                ? [{ value: "__global__", label: "(global)" }]
                : []),
              ...nodeOptions.sorted.map((n) => ({ value: n, label: n })),
            ]}
            minWidth={180}
          />
          <button type="button" className="btn-secondary" onClick={clearDisplayFilters}>
            Clear display filters
          </button>
        </div>

        <div className="toolbar" style={{ marginBottom: "0.75rem" }}>
          <span className="meta" style={{ alignSelf: "center", marginRight: "0.25rem" }}>
            Analysis (re-fetch):
          </span>
          <AppCheckbox
            inline
            title="API: only return rules whose screens gate matches current_screen"
            checked={onlyCurrentScreen}
            onChange={setOnlyCurrentScreen}
            disabled={ignoreScreenGate}
            label="Only current screen (API)"
          />
          <AppCheckbox
            inline
            title="API: run every rule even when screens gate would skip it"
            checked={ignoreScreenGate}
            onChange={setIgnoreScreenGate}
            label="Ignore screen gate (API)"
          />
          <AppCheckbox
            inline
            title="Client: hide rows whose node does not match current_screen (globals stay visible)"
            checked={onlyCurrentNode}
            onChange={setOnlyCurrentNode}
            disabled={ignoreScreenGate}
            label="Only current node (+ globals)"
          />
        </div>

        <div>
          <div className="meta" style={{ marginBottom: "0.35rem" }}>
            Action types
          </div>
          <div className="toolbar" style={{ gap: "0.35rem" }}>
            {OVERLAY_ACTION_TYPES.map((act) => {
              const on = actionsVisible[act] !== false;
              return (
                <button
                  key={act}
                  type="button"
                  className={on ? "btn-primary" : "btn-secondary"}
                  style={{ padding: "0.25rem 0.6rem", fontSize: "0.8rem" }}
                  onClick={() => toggleAction(act)}
                  aria-pressed={on}
                >
                  {act}
                </button>
              );
            })}
            <button
              type="button"
              className="btn-secondary"
              style={{ padding: "0.25rem 0.6rem", fontSize: "0.8rem" }}
              onClick={() => setActionsVisible(defaultActionVisibility())}
            >
              All actions
            </button>
            <button
              type="button"
              className="btn-secondary"
              style={{ padding: "0.25rem 0.6rem", fontSize: "0.8rem" }}
              onClick={() =>
                setActionsVisible(
                  Object.fromEntries(OVERLAY_ACTION_TYPES.map((a) => [a, false])),
                )
              }
            >
              None
            </button>
          </div>
        </div>

        {highlightRule ? (
          <p className="meta" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
            Canvas highlight: <code>{highlightRule}</code>{" "}
            <button
              type="button"
              className="btn-secondary"
              style={{ padding: "0.15rem 0.5rem", fontSize: "0.75rem", marginLeft: "0.35rem" }}
              onClick={() => setHighlightRule(null)}
            >
              Show all filtered
            </button>
          </p>
        ) : (
          <p className="meta" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
            Click a rule row to highlight it on the canvas.
          </p>
        )}
      </section>

      <div className="approvals-grid">
        <section className="panel">
          <h2>Screen</h2>
          <ApprovalCanvas
            imageUrl={imageUrl}
            width={result?.preview.width ?? 0}
            height={result?.preview.height ?? 0}
            overlays={canvasOverlays}
          />
          <p className="meta">
            <span style={{ color: "#22c55e" }}>■ matched</span>{" "}
            <span style={{ color: "#f59e0b" }}>■ search ROI</span>{" "}
            <span style={{ color: "#3b82f6" }}>■ region bbox</span>{" "}
            <span style={{ color: "#ff0000" }}>+ tap target</span>
          </p>
        </section>

        <section className="panel">
          <h2>
            Rules ({sortedRules.length}
            {result && sortedRules.length !== result.rules.length
              ? ` / ${result.rules.length}`
              : ""}
            )
          </h2>
          {sortedRules.length === 0 ? (
            <p className="meta">
              No rules match — clear filters or adjust analysis options, then refresh.
            </p>
          ) : (
            <div className="data-table-wrap" style={{ maxHeight: 640, overflowY: "auto" }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Rule</th>
                    <th>Region</th>
                    <th>Action</th>
                    <th>Score</th>
                    <th>Threshold</th>
                    <th>Node</th>
                    <th>Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedRules.map((r) => {
                    const active = highlightRule === r.name;
                    return (
                      <tr
                        key={r.name}
                        onClick={() =>
                          setHighlightRule((prev) => (prev === r.name ? null : r.name))
                        }
                        style={{
                          cursor: "pointer",
                          background: active ? "rgba(59, 130, 246, 0.12)" : undefined,
                        }}
                        title="Click to highlight on canvas"
                      >
                        <td>
                          <span
                            className={`status-pill ${r.matched ? "pill-live" : "pill-stale"}`}
                          >
                            {r.matched ? "✓" : "✗"}
                          </span>
                        </td>
                        <td>
                          <code>{r.name}</code>
                        </td>
                        <td>
                          <code>{r.region || "—"}</code>
                        </td>
                        <td>{r.action || "—"}</td>
                        <td>{r.score?.toFixed(3) ?? "—"}</td>
                        <td>{r.threshold?.toFixed(3) ?? "—"}</td>
                        <td>{r.node || "(global)"}</td>
                        <td>{r.notes || "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
