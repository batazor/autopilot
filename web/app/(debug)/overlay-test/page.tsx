"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApprovalCanvas } from "@/components/ApprovalCanvas";
import { CopyButton } from "@/components/CopyButton";
import { AppCheckbox, AppCombobox, AppListbox } from "@/components/headless";
import { useFleet } from "@/components/FleetContextProvider";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { fetchGallery, fetchOverlayTest, overlayTestImageUrl } from "@/lib/api";
import type { GalleryItem } from "@/lib/config-pages";
import { useStableCacheKey } from "@/lib/hooks";
import {
  defaultActionVisibility,
  overlayLabelRuleName,
  OVERLAY_ACTION_TYPES,
  type MatchStatusFilter,
} from "@/lib/overlay-test";
import type { OverlayRuleRow, OverlayShape, OverlayTestResult } from "@/lib/types";

const POLL_MS = 1500;
const LIVE_PREVIEW_KEY = "__live__";

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
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const regionParam =
    searchParams.get("region") ?? searchParams.get("highlight");
  const refFromUrl = (searchParams.get("ref") ?? "").trim();
  const { instanceId, instancesError } = useFleet();
  const [result, setResult] = useState<OverlayTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const [onlyCurrentScreen, setOnlyCurrentScreen] = useState(false);
  const [ignoreScreenGate, setIgnoreScreenGate] = useState(false);
  const [hasActivePlayer, setHasActivePlayer] = useState(true);

  const [textFilter, setTextFilter] = useState("");
  const [matchStatus, setMatchStatus] = useState<MatchStatusFilter>("all");
  const [onlyCurrentNode, setOnlyCurrentNode] = useState(true);
  const [nodeFilter, setNodeFilter] = useState("");
  const [actionsVisible, setActionsVisible] = useState(defaultActionVisibility);
  const [highlightRule, setHighlightRule] = useState<string | null>(null);
  const [frameKey, setFrameKey] = useState(() =>
    refFromUrl ? refFromUrl : LIVE_PREVIEW_KEY,
  );
  const [galleryItems, setGalleryItems] = useState<GalleryItem[]>([]);

  const syncRefInUrl = useCallback(
    (key: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (key === LIVE_PREVIEW_KEY) params.delete("ref");
      else params.set("ref", key);
      const q = params.toString();
      router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const setFrameKeyAndUrl = useCallback(
    (key: string) => {
      setFrameKey(key);
      syncRefInUrl(key);
    },
    [syncRefInUrl],
  );

  useEffect(() => {
    setFrameKey(refFromUrl || LIVE_PREVIEW_KEY);
  }, [refFromUrl]);

  const previewSource =
    frameKey === LIVE_PREVIEW_KEY ? ("live" as const) : ("reference" as const);
  const previewRel = frameKey === LIVE_PREVIEW_KEY ? undefined : frameKey;

  useEffect(() => {
    let cancelled = false;
    void fetchGallery("all", "")
      .then((data) => {
        if (!cancelled) setGalleryItems(data.items);
      })
      .catch(() => {
        if (!cancelled) setGalleryItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Pre-compute a stable haystack per item; AppCombobox handles its own query
  // filtering and matches on both label and value, so we extend the value with
  // screen IDs to keep "search by screen_id" working without a separate input.
  const referenceOptions = useMemo(() => {
    const options = [
      { value: LIVE_PREVIEW_KEY, label: "Rolling preview (live)" },
      ...galleryItems.map((item) => ({
        value: item.rel,
        label: `${item.name} · ${item.group}`,
      })),
    ];
    if (
      frameKey !== LIVE_PREVIEW_KEY &&
      !options.some((o) => o.value === frameKey)
    ) {
      options.push({ value: frameKey, label: frameKey });
    }
    return options;
  }, [galleryItems, frameKey]);

  const referenceFilterTokens = useMemo(() => {
    const map = new Map<string, string>();
    map.set(LIVE_PREVIEW_KEY, "rolling preview live");
    for (const item of galleryItems) {
      map.set(
        item.rel,
        `${item.rel} ${item.name} ${item.group} ${item.screen_ids.join(" ")}`.toLowerCase(),
      );
    }
    return map;
  }, [galleryItems]);

  const filterReferenceOption = useCallback(
    (option: { value: string; label: string }, query: string) => {
      const needle = query.trim().toLowerCase();
      if (!needle) return true;
      const hay =
        referenceFilterTokens.get(option.value) ??
        `${option.value} ${option.label}`.toLowerCase();
      return hay.includes(needle);
    },
    [referenceFilterTokens],
  );

  const referenceShareUrl = useMemo(() => {
    if (frameKey === LIVE_PREVIEW_KEY) return "";
    const params = new URLSearchParams(searchParams.toString());
    params.set("ref", frameKey);
    const q = params.toString();
    if (typeof window === "undefined") return `${pathname}?${q}`;
    return `${window.location.origin}${pathname}?${q}`;
  }, [frameKey, pathname, searchParams]);

  useEffect(() => {
    if (!regionParam?.trim()) return;
    const r = regionParam.trim();
    setTextFilter(r);
    setHighlightRule(r);
    setOnlyCurrentNode(false);
  }, [regionParam]);

  const overlayQuery = useQuery({
    queryKey: [
      "overlayTest",
      instanceId,
      onlyCurrentScreen,
      ignoreScreenGate,
      hasActivePlayer,
      previewSource,
      previewRel,
    ],
    queryFn: () =>
      fetchOverlayTest(instanceId, {
        onlyCurrentScreen: onlyCurrentScreen && !ignoreScreenGate,
        ignoreScreenGate,
        hasActivePlayer,
        detailedAnalysis: false,
        previewSource,
        previewRel,
      }),
    enabled: !!instanceId,
    refetchInterval: autoRefresh ? POLL_MS : false,
  });

  useEffect(() => {
    if (overlayQuery.data) setResult(overlayQuery.data);
  }, [overlayQuery.data]);

  useEffect(() => {
    if (overlayQuery.isError) {
      setError(
        overlayQuery.error instanceof Error
          ? overlayQuery.error.message
          : String(overlayQuery.error),
      );
    } else if (overlayQuery.isSuccess) {
      setError(null);
    }
  }, [overlayQuery.isError, overlayQuery.isSuccess, overlayQuery.error]);

  const analyzeMutation = useMutation({
    mutationFn: () => {
      if (!instanceId) throw new Error("no instance selected");
      return fetchOverlayTest(instanceId, {
        onlyCurrentScreen: onlyCurrentScreen && !ignoreScreenGate,
        ignoreScreenGate,
        hasActivePlayer,
        detailedAnalysis: true,
        previewSource,
        previewRel,
      });
    },
    onSuccess: (data) => {
      setResult(data);
      setError(null);
    },
    onError: (e) => {
      setError(e instanceof Error ? e.message : String(e));
    },
  });

  const analyzing = analyzeMutation.isPending;
  const analyzeScreenshot = useCallback(() => {
    analyzeMutation.mutate();
  }, [analyzeMutation]);

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
      ? overlayTestImageUrl(instanceId, previewCacheKey, {
          previewSource,
          previewRel,
        })
      : null;

  const analysisJson = useMemo(() => {
    if (!result?.analysis) return "";
    const payload = {
      instance_id: result.instance_id,
      current_screen: result.current_screen,
      detected_screen: result.detected_screen,
      active_player: result.active_player,
      matched_count: result.matched_count,
      total_rules: result.total_rules,
      ...result.analysis,
    };
    return JSON.stringify(payload, null, 2);
  }, [result]);

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

      <section className="panel" style={{ marginBottom: "1rem" }}>
        <h2 style={{ marginTop: 0 }}>Frame</h2>
        <div className="toolbar" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
          <AppCombobox
            label="Image"
            value={frameKey}
            onChange={setFrameKeyAndUrl}
            options={referenceOptions}
            filter={filterReferenceOption}
            placeholder="Type path, screen, module…"
            minWidth={360}
            title={frameKey === LIVE_PREVIEW_KEY ? "ADB rolling preview" : frameKey}
          />
          {referenceShareUrl ? (
            <CopyButton
              text={referenceShareUrl}
              label="Copy link"
              title="Copy overlay-test URL with this reference image"
            />
          ) : null}
          {result?.preview.rel ? (
            <span className="meta" style={{ alignSelf: "center" }}>
              {result.preview.source === "reference" ? "reference" : "live"}:{" "}
              <code>{result.preview.rel}</code>
            </span>
          ) : null}
        </div>
      </section>

      <div className="toolbar">
        <AppCheckbox
          inline
          checked={autoRefresh}
          onChange={setAutoRefresh}
          label="Auto-refresh"
          disabled={previewSource === "reference"}
          title={
            previewSource === "reference"
              ? "Auto-refresh applies to the live rolling preview only"
              : undefined
          }
        />
        <button
          type="button"
          className="btn-primary"
          onClick={analyzeScreenshot}
          disabled={analyzing || !instanceId}
          title="Run overlay analyzers on the rolling screenshot (per-module timing + queue dry-run)"
        >
          {analyzing ? "Analyzing…" : "Analyze screenshot"}
        </button>
        {result ? (
          <span className="meta">
            screen: <code>{result.detected_screen || "—"}</code>
            {result.analysis?.screen_detect_ms != null ? (
              <>
                {" "}
                · detect <code>{result.analysis.screen_detect_ms} ms</code>
              </>
            ) : null}
          </span>
        ) : null}
      </div>

      {result?.analysis ? (
        <section className="panel" style={{ marginBottom: "1rem" }}>
          <div
            className="toolbar"
            style={{ marginBottom: "0.75rem", flexWrap: "wrap", alignItems: "center" }}
          >
            <h2 style={{ margin: 0 }}>Analyzer run</h2>
            <CopyButton
              text={analysisJson}
              label="Copy JSON"
              title="Copy analyzer run results as JSON"
            />
          </div>
          <div className="toolbar" style={{ marginBottom: "0.75rem", flexWrap: "wrap" }}>
            <AppCheckbox
              inline
              checked={hasActivePlayer}
              onChange={setHasActivePlayer}
              label="Active player / player ID known"
              title="When off, overlay cond gates see empty active_player (boot/login ads). Does not read Redis — probe flag only."
            />
            {result.analysis.simulated_no_player ? (
              <span className="status-pill pill-stale">
                probe: no player
                {result.analysis.device_level_only ? " · device-level overlay only" : ""}
              </span>
            ) : (
              <span className="status-pill pill-live">probe: player known (synthetic)</span>
            )}
            <span className="meta">
              screen gate: <code>{result.current_screen || "—"}</code>
              {result.analysis.screen_source ? (
                <>
                  {" "}
                  (<code>{result.analysis.screen_source}</code>)
                </>
              ) : null}
              {" · "}
              detect <code>{result.analysis.screen_detect_ms ?? 0} ms</code>
              {" · "}
              full pass <code>{result.analysis.full_run_ms} ms</code>
              {" · "}
              per-module sum <code>{result.analysis.modules_total_ms} ms</code>
            </span>
          </div>

          <p className="meta" style={{ marginBottom: "0.75rem" }}>
            Static frame probe — screen gates use detection on this PNG only (no
            instance Redis state).
          </p>

          {result.analysis.module_runs.length === 0 &&
          result.analysis.modules_total_ms === 0 ? (
            <p className="meta" style={{ marginBottom: "1rem" }}>
              Per-module timing appears after <strong>Analyze screenshot</strong> (not on
              auto-refresh).
            </p>
          ) : null}
          {result.analysis.module_runs.length > 0 ? (
            <div className="data-table-wrap" style={{ marginBottom: "1rem" }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Module</th>
                    <th>Duration</th>
                    <th>Rules</th>
                    <th>Matched</th>
                  </tr>
                </thead>
                <tbody>
                  {[...result.analysis.module_runs]
                    .sort((a, b) => b.duration_ms - a.duration_ms)
                    .map((row) => (
                      <tr key={row.module_id}>
                        <td>
                          <code>{row.module_id}</code>
                          {row.label !== row.module_id ? (
                            <span className="meta"> — {row.label}</span>
                          ) : null}
                        </td>
                        <td>{row.duration_ms} ms</td>
                        <td>{row.rule_count}</td>
                        <td>{row.matched_count}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="meta">No module analyzers ran — capture a rolling preview first.</p>
          )}

          <h3 style={{ fontSize: "1rem", marginBottom: "0.5rem" }}>Queue push candidates (dry-run)</h3>
          {result.analysis.push_candidates.length === 0 ? (
            <p className="meta">No matched overlay rules with pushScenario on this frame.</p>
          ) : (
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Pick</th>
                    <th>Scenario</th>
                    <th>Rule</th>
                    <th>Priority</th>
                    <th>Region</th>
                    <th>Note</th>
                  </tr>
                </thead>
                <tbody>
                  {result.analysis.push_candidates.map((row) => (
                    <tr key={`${row.rule}:${row.scenario}`}>
                      <td>
                        {row.selected ? (
                          <span className="status-pill pill-live">would push</span>
                        ) : (
                          <span className="status-pill pill-stale">—</span>
                        )}
                      </td>
                      <td>
                        <code>{row.scenario}</code>
                      </td>
                      <td>
                        <code>{row.rule}</code>
                      </td>
                      <td>{row.priority}</td>
                      <td>
                        <code>{row.region || "—"}</code>
                      </td>
                      <td>{row.skip_reason || (row.selected ? "selected" : "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}

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
