"use client";

import Link from "next/link";
import { memo, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useTheme } from "@/components/ThemeProvider";
import { AppListbox } from "@/components/headless";
import { tip } from "@/components/AppTooltip";
import { Icon } from "@/components/ui/Icon";
import {
  editDslRegionPreviewUrl,
  fetchAreaRegionProbe,
  fetchEditDslCallers,
  fetchInstanceDetail,
  fetchInstances,
  fetchLabelingDocument,
  fetchQueue,
  fetchRegionOcr,
  labelingImageUrl,
} from "@/lib/api";
import type { PercentBBox } from "@/lib/bbox";
import { inferScopeFromRef } from "@/lib/labeling-utils";
import type { InstanceDetail, QueueHistoryRow } from "@/lib/types";
import {
  traceStatusTip,
  traceToNodeStatuses,
  type NodeTraceStatus,
} from "@/lib/edit-dsl/trace";
import {
  LOOP_PARENT_KINDS,
  STEP_TYPES_FOR_NEW,
  detectStepType,
  newStep,
  type ScenarioDocument,
} from "@/lib/edit-dsl/dsl";
import {
  START_NODE_ID,
  docToFlow,
  duplicateStepAt,
  getStepAt,
  insertStepAt,
  isContainerStep,
  listAt,
  moveStepAt,
  parsePathKey,
  pathKey,
  removeStepAt,
  updateStepAt,
  WRAP_KINDS,
  wrapStepAt,
  type DslContainerNodeData,
  type DslStartNodeData,
  type DslStepNodeData,
  type StepPath,
  type WrapKind,
} from "@/lib/edit-dsl/flow";
import { ScenarioHeaderForm } from "./ScenarioHeaderForm";
import { StepCard, type EditorMeta } from "./StepCard";

const CANVAS_H = 640;

function Chip({ tone, children }: { tone: "accent" | "warn" | "ok"; children: React.ReactNode }) {
  const color =
    tone === "warn" ? "#f59e0b" : tone === "ok" ? "#22c55e" : "var(--wos-accent)";
  return (
    <span
      className="rounded px-1 text-[10px] leading-4 whitespace-nowrap"
      style={{
        color,
        background: `color-mix(in srgb, ${color} 14%, transparent)`,
      }}
    >
      {children}
    </span>
  );
}

function IssueChip({ issues }: { issues: string[] }) {
  if (!issues.length) return null;
  return (
    <span
      className="rounded px-1 text-[10px] leading-4 whitespace-nowrap"
      style={{
        color: "#ef4444",
        background: "color-mix(in srgb, #ef4444 16%, transparent)",
      }}
      {...tip(issues.join("\n"))}
    >
      ⚠ {issues.length}
    </span>
  );
}

function nodeIssues(data: Record<string, unknown>): string[] {
  return Array.isArray(data.issues) ? (data.issues as string[]) : [];
}

function RegionThumb({ region }: { region: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={editDslRegionPreviewUrl(region)}
      alt={region}
      className="h-9 w-9 shrink-0 rounded object-contain"
      style={{ background: "var(--wos-surface)" }}
      onError={() => setBroken(true)}
    />
  );
}

const DslStartNode = memo(function DslStartNode({ data }: NodeProps) {
  const d = data as DslStartNodeData;
  return (
    <div
      className="flex h-full flex-col justify-center gap-1 rounded-lg border-2 px-3 py-2"
      style={{
        background: "var(--wos-panel-raised)",
        borderColor: "var(--wos-accent)",
      }}
    >
      <div className="flex items-center gap-2">
        <span
          className="h-2 w-2 shrink-0 rounded-full"
          style={{ background: d.enabled ? "#22c55e" : "#f59e0b" }}
          {...tip(d.enabled ? "enabled" : "disabled")}
        />
        <strong className="truncate text-sm">{d.name || "Untitled scenario"}</strong>
      </div>
      <div className="flex items-center gap-1 overflow-hidden text-xs">
        <Chip tone="accent">{d.node || "anywhere"}</Chip>
        {d.cron ? <Chip tone="accent">{d.cron}</Chip> : null}
        {d.deviceLevel ? <Chip tone="warn">device</Chip> : null}
        <span className="muted whitespace-nowrap">{d.stepCount} steps</span>
        <IssueChip issues={nodeIssues(d)} />
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});

const DslStepNode = memo(function DslStepNode({ data, selected }: NodeProps) {
  const d = data as DslStepNodeData;
  const live = Boolean(d.live);
  const issues = nodeIssues(d);
  return (
    <div
      className="flex h-full items-center gap-2 rounded-lg border p-2"
      style={{
        background: live
          ? "color-mix(in srgb, #22c55e 10%, var(--wos-panel-raised))"
          : "var(--wos-panel-raised)",
        borderColor: selected
          ? "var(--wos-accent)"
          : live
            ? "#22c55e"
            : issues.length
              ? "#ef4444aa"
              : "var(--wos-border)",
        boxShadow: selected
          ? "0 0 0 2px var(--wos-accent)"
          : live
            ? "0 0 0 2px #22c55e88"
            : undefined,
      }}
    >
      <Handle type="target" position={Position.Top} />
      <NodeIndexBadge
        label={live ? "▶" : d.index}
        trace={live ? null : nodeTrace(d)}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1">
          <code className="text-xs font-semibold">{d.kind}</code>
          {d.cond ? <Chip tone="warn">cond</Chip> : null}
          {d.redDot !== null ? (
            <Chip tone={d.redDot ? "ok" : "warn"}>red-dot {d.redDot ? "on" : "off"}</Chip>
          ) : null}
          <IssueChip issues={issues} />
        </div>
        <div className="muted truncate text-xs" {...(d.summary ? tip(d.summary) : {})}>
          {d.summary || "—"}
        </div>
      </div>
      {d.kind === "push_scenario" && d.summary && !issues.length ? (
        <Link
          href={`/edit-dsl?scenario=${encodeURIComponent(d.summary)}`}
          className="region-labeling-link shrink-0 text-xs"
          onClick={(e) => e.stopPropagation()}
          {...tip(`Open ${d.summary}`)}
        >
          ↗
        </Link>
      ) : null}
      {d.region ? <RegionThumb region={d.region} /> : null}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});

const DslContainerNode = memo(function DslContainerNode({ data, selected }: NodeProps) {
  const d = data as DslContainerNodeData;
  const live = Boolean(d.live);
  const issues = nodeIssues(d);
  return (
    <div
      className="h-full w-full rounded-xl border border-dashed"
      style={{
        background: "color-mix(in srgb, var(--wos-accent) 5%, transparent)",
        borderColor: selected
          ? "var(--wos-accent)"
          : live
            ? "#22c55e"
            : issues.length
              ? "#ef4444aa"
              : "var(--wos-border)",
        boxShadow: selected
          ? "0 0 0 2px var(--wos-accent)"
          : live
            ? "0 0 0 2px #22c55e88"
            : undefined,
      }}
    >
      <Handle type="target" position={Position.Top} />
      <div className="flex items-center gap-2 overflow-hidden px-3" style={{ height: 48 }}>
        <NodeIndexBadge
          label={live ? "▶" : d.index}
          trace={live ? null : nodeTrace(d)}
        />
        <code className="text-xs font-semibold">{d.kind}</code>
        {d.title ? (
          <span className="muted truncate text-xs" {...tip(d.title)}>
            {d.title}
          </span>
        ) : null}
        {d.detail ? <Chip tone="accent">{d.detail}</Chip> : null}
        {d.cond ? <Chip tone="warn">cond</Chip> : null}
        <IssueChip issues={issues} />
      </div>
      {d.childCount === 0 ? (
        <div className="muted px-3 text-xs">no inner steps — select and add one</div>
      ) : null}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});

const nodeTypes = {
  dslStart: DslStartNode,
  dslStep: DslStepNode,
  dslContainer: DslContainerNode,
};

/** Reference screenshot of the region's screen with the region framed —
 *  shows *where on the screen* the selected step acts, without leaving the
 *  editor. Geometry comes from the labeling document (percent bbox). */
function RegionScreenPreview({ region, meta }: { region: string; meta: EditorMeta }) {
  const ref = meta.region_refs?.[region] ?? null;
  const scope = ref ? (inferScopeFromRef(ref) ?? "core") : "core";
  const { data } = useQuery({
    queryKey: ["edit-dsl-labeling-doc", ref, scope],
    queryFn: () => fetchLabelingDocument(ref!, scope),
    enabled: !!ref,
    staleTime: 5 * 60_000,
  });
  if (!ref) return null;
  const reg = data?.regions?.find((r) => String(r.name ?? "") === region);
  const bbox = (reg?.bbox ?? null) as PercentBBox | null;
  const screen = meta.region_screens?.[region];
  return (
    <div className="mt-3">
      <div className="muted mb-1 flex items-center gap-2 text-xs">
        <span>
          on screen <code>{screen || "?"}</code>
        </span>
        <Link
          href={`/labeling?ref=${encodeURIComponent(ref)}&region=${encodeURIComponent(region)}`}
          className="region-labeling-link"
          target="_blank"
          rel="noreferrer"
        >
          labeling ↗
        </Link>
      </div>
      <div
        className="relative overflow-hidden rounded border"
        style={{ borderColor: "var(--wos-border)", maxWidth: 280 }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={labelingImageUrl(ref)} alt={region} className="block w-full" />
        {bbox ? (
          <div
            className="pointer-events-none absolute rounded-sm border-2"
            style={{
              left: `${bbox.x}%`,
              top: `${bbox.y}%`,
              width: `${bbox.width}%`,
              height: `${bbox.height}%`,
              borderColor: "var(--wos-accent)",
              boxShadow:
                "0 0 0 9999px color-mix(in srgb, var(--wos-bg) 55%, transparent)",
            }}
          />
        ) : null}
      </div>
    </div>
  );
}

const TRACE_COLORS: Record<NodeTraceStatus["bucket"], string> = {
  ok: "#22c55e",
  failed: "#ef4444",
  skipped: "#64748b",
};

/** Index circle shared by step/container nodes — colored by the node's
 *  last-run trace status when one is known. */
function NodeIndexBadge({
  label,
  trace,
}: {
  label: React.ReactNode;
  trace: NodeTraceStatus | null;
}) {
  return (
    <span
      className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px]"
      style={
        trace
          ? {
              background: `color-mix(in srgb, ${TRACE_COLORS[trace.bucket]} 25%, var(--wos-surface))`,
              color: TRACE_COLORS[trace.bucket],
              fontWeight: 600,
            }
          : { background: "var(--wos-surface)", color: "var(--wos-text-muted)" }
      }
      {...(trace ? tip(traceStatusTip(trace)) : {})}
    >
      {label}
    </span>
  );
}

function nodeTrace(data: Record<string, unknown>): NodeTraceStatus | null {
  return (data.trace as NodeTraceStatus | undefined) ?? null;
}

function agoLabel(unixSeconds: number): string {
  const s = Math.max(0, Math.round(Date.now() / 1000 - unixSeconds));
  if (s < 90) return `${s}s ago`;
  if (s < 90 * 60) return `${Math.round(s / 60)}m ago`;
  if (s < 36 * 3600) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/** Latest finished queue-history run of this scenario that carries a
 *  step-level trace. Polled while the canvas is mounted. */
function useLastRun(stem: string, docName: string): QueueHistoryRow | null {
  const { data } = useQuery({
    queryKey: ["edit-dsl-last-run", stem],
    queryFn: () => fetchQueue({ historyPageSize: 100 }),
    refetchInterval: 10_000,
    staleTime: 8_000,
  });
  if (!data || !("history" in data)) return null;
  let best: QueueHistoryRow | null = null;
  for (const row of data.history) {
    if (row.scenario_key !== stem && row.scenario !== docName) continue;
    if (!Array.isArray(row.steps_trace) || !row.steps_trace.length) continue;
    if (!best || row.finished_at > best.finished_at) best = row;
  }
  return best;
}

/** Reverse references — what can start this scenario. Shown with the header
 *  form so "can I rename/delete this?" is answered before editing. */
function CallersPanel({ rel }: { rel: string }) {
  const { data } = useQuery({
    queryKey: ["edit-dsl-callers", rel],
    queryFn: () => fetchEditDslCallers(rel),
    staleTime: 60_000,
  });
  if (!data) return null;
  const { callers, cron, notify_events: notifyEvents } = data;
  const empty = !callers.length && !cron && !notifyEvents.length;
  return (
    <div className="mt-3 border-t pt-2" style={{ borderColor: "var(--wos-border)" }}>
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--wos-text-muted)" }}>
        Called by
      </div>
      {empty ? (
        <p className="muted m-0 text-xs">
          Nothing references this scenario — it only runs when pushed manually.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
          {callers.map((c) => (
            <li key={`${c.rel}:${c.step}`} className="flex items-center gap-2">
              <Link
                href={`/edit-dsl?scenario=${encodeURIComponent(c.rel)}`}
                className="region-labeling-link truncate"
                title={c.rel}
              >
                {c.stem}
              </Link>
              <code className="muted shrink-0 text-xs">step {c.step}</code>
            </li>
          ))}
          {cron ? (
            <li className="flex items-center gap-2">
              <Chip tone="accent">cron</Chip>
              <code className="text-xs">{cron}</code>
            </li>
          ) : null}
          {notifyEvents.map((ev) => (
            <li key={ev} className="flex items-center gap-2">
              <Chip tone="warn">notify</Chip>
              <code className="text-xs">{ev}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type RegionTestOutcome = {
  ok: boolean;
  summary: string;
  detail: string;
};

/** "Test now" — run the step's region against the device's current frame:
 *  template probe for match/click-style steps, live OCR for `ocr` steps.
 *  Answers "why doesn't this match?" without leaving the editor. */
function RegionTestPanel({
  region,
  kind,
  threshold,
}: {
  region: string;
  kind: string;
  threshold: number | null;
}) {
  const { data: instances } = useQuery({
    queryKey: ["edit-dsl-live-instances"],
    queryFn: fetchInstances,
    staleTime: 25_000,
  });
  const [instanceId, setInstanceId] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<RegionTestOutcome | null>(null);
  const effectiveInstance = instanceId || instances?.[0] || "";

  // Result belongs to one (region, kind) — drop it when the selection moves.
  useEffect(() => {
    setOutcome(null);
  }, [region, kind]);

  if (!instances?.length) return null;

  const runTest = async () => {
    setBusy(true);
    setOutcome(null);
    try {
      if (kind === "ocr") {
        const r = await fetchRegionOcr(effectiveInstance, [region]);
        const row = r.rows.find((x) => x.region === region) ?? r.rows[0];
        setOutcome(
          row
            ? {
                ok: !!row.text && !row.low_confidence,
                summary: row.text ? `"${row.text}"` : "(no text)",
                detail: [
                  row.confidence !== null ? `confidence ${row.confidence}` : "",
                  row.status,
                  row.duration_ms !== null ? `${row.duration_ms} ms` : "",
                ]
                  .filter(Boolean)
                  .join(" · "),
              }
            : { ok: false, summary: "no OCR result", detail: "" },
        );
      } else {
        const r = await fetchAreaRegionProbe(effectiveInstance, {
          region,
          ...(threshold ? { threshold } : {}),
        });
        const res = r.result;
        setOutcome(
          res
            ? {
                ok: !!res.matched,
                summary: res.matched ? "matched" : "not matched",
                detail: [
                  res.score !== undefined ? `score ${Number(res.score).toFixed(3)}` : "",
                  res.threshold !== undefined ? `threshold ${res.threshold}` : "",
                  res.reason ?? "",
                  res.red_dot_present !== undefined
                    ? `red-dot ${res.red_dot_present ? "on" : "off"}`
                    : "",
                ]
                  .filter(Boolean)
                  .join(" · "),
              }
            : { ok: false, summary: "no probe result", detail: "" },
        );
      }
    } catch (e) {
      setOutcome({
        ok: false,
        summary: "test failed",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {instances.length > 1 ? (
          <AppListbox
            inline
            label="Device"
            value={effectiveInstance}
            onChange={setInstanceId}
            options={instances.map((id) => ({ value: id, label: id }))}
            minWidth={110}
          />
        ) : null}
        <button
          type="button"
          className="btn-secondary"
          disabled={busy || !effectiveInstance}
          onClick={() => void runTest()}
          {...tip(`Run ${kind === "ocr" ? "OCR" : "template match"} on the live frame`)}
        >
          {busy ? "Testing…" : `Test on ${effectiveInstance}`}
        </button>
      </div>
      {outcome ? (
        <div
          className="rounded-lg border px-2 py-1 text-xs"
          style={{
            borderColor: outcome.ok ? "#22c55e55" : "#ef444455",
            background: `color-mix(in srgb, ${outcome.ok ? "#22c55e" : "#ef4444"} 8%, var(--wos-panel-raised))`,
          }}
        >
          <strong style={{ color: outcome.ok ? "#22c55e" : "#ef4444" }}>
            {outcome.summary}
          </strong>
          {outcome.detail ? <span className="muted"> · {outcome.detail}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

type LiveTrace = { instanceId: string; step: number | null; iter: string };

/** Poll instance state while the canvas is mounted; if some instance is
 *  currently running this scenario (worker publishes `current_scenario` +
 *  `last_active_scenario_step`), surface device + top-level step index. */
function useLiveTrace(stem: string, docName: string): LiveTrace | null {
  const ids = useQuery({
    queryKey: ["edit-dsl-live-instances"],
    queryFn: fetchInstances,
    refetchInterval: 30_000,
    staleTime: 25_000,
  });
  const details = useQuery({
    queryKey: ["edit-dsl-live-state", ids.data],
    queryFn: async () => {
      const rows = await Promise.all(
        (ids.data ?? []).map((id) => fetchInstanceDetail(id).catch(() => null)),
      );
      return rows.filter(
        (r): r is InstanceDetail => !!r && typeof r === "object" && "state" in r,
      );
    },
    enabled: !!ids.data?.length,
    refetchInterval: 2_500,
  });
  for (const d of details.data ?? []) {
    const st = d.state ?? {};
    const cur = (st.current_scenario ?? "").trim();
    if (!cur || (cur !== stem && cur !== docName)) continue;
    const idx = parseInt(st.last_active_scenario_step ?? "", 10);
    return {
      instanceId: d.instance_id,
      step: Number.isFinite(idx) ? idx : null,
      iter: (st.last_active_scenario_iter ?? "").trim(),
    };
  }
  return null;
}

type Props = {
  doc: ScenarioDocument;
  rel: string;
  meta: EditorMeta;
  collisions: string[];
  onCollisionsChange: (rels: string[]) => void;
  onChange: (doc: ScenarioDocument) => void;
};

export function ScenarioFlow({
  doc,
  rel,
  meta,
  collisions,
  onCollisionsChange,
  onChange,
}: Props) {
  const { theme } = useTheme();
  // "start" pins the header form; a path key pins that step; null = overview.
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [addType, setAddType] = useState<string>(STEP_TYPES_FOR_NEW[0]);

  const { nodes, edges } = useMemo(() => docToFlow(doc, meta), [doc, meta]);

  const stem = (rel.split("/").pop() ?? "").replace(/\.ya?ml$/, "");
  const docName = String(doc.name ?? "").trim();
  const live = useLiveTrace(stem, docName);
  const liveNodeId =
    live && live.step !== null && Array.isArray(doc.steps) && live.step < doc.steps.length
      ? `s:${live.step}`
      : null;

  const lastRun = useLastRun(stem, docName);
  const [showTrace, setShowTrace] = useState(true);
  const traceStatuses = useMemo(
    () =>
      lastRun && showTrace
        ? traceToNodeStatuses(doc, lastRun.steps_trace ?? [])
        : null,
    [lastRun, showTrace, doc],
  );

  const selectedPath: StepPath | null =
    selectedKey !== null && selectedKey !== "start" ? parsePathKey(selectedKey) : null;
  const selectedStep = selectedPath ? getStepAt(doc, selectedPath) : null;

  const selectedNodeId =
    selectedKey === "start"
      ? START_NODE_ID
      : selectedStep && selectedPath
        ? `s:${pathKey(selectedPath)}`
        : null;
  const rfNodes = useMemo(
    () =>
      nodes.map((n) => {
        const trace = n.id.startsWith("s:")
          ? (traceStatuses?.get(n.id.slice(2)) ?? null)
          : null;
        const isLive = n.id === liveNodeId;
        const isSelected = n.id === selectedNodeId;
        if (!trace && !isLive && !isSelected) return n;
        return {
          ...n,
          ...(isSelected ? { selected: true } : {}),
          data: {
            ...n.data,
            ...(isLive ? { live: true } : {}),
            ...(trace ? { trace } : {}),
          },
        };
      }),
    [nodes, selectedNodeId, liveNodeId, traceStatuses],
  );

  // Where "add step" lands: after the selected step, inside the selected
  // container, or at the end of the root list when nothing is pinned.
  const parentPath = selectedPath ? selectedPath.slice(0, -1) : [];
  const parentStep = parentPath.length ? getStepAt(doc, parentPath) : null;
  const parentKind = parentStep ? detectStepType(parentStep) : "";
  const addTypes = [
    ...STEP_TYPES_FOR_NEW,
    ...(LOOP_PARENT_KINDS.has(parentKind) ? (["break"] as const) : []),
  ];

  const selectStep = (path: StepPath) => setSelectedKey(pathKey(path));

  const handleAddAfter = () => {
    if (!selectedPath) {
      const end = listAt(doc, []).length;
      onChange(insertStepAt(doc, [], end, newStep(addType)));
      selectStep([end]);
      return;
    }
    const idx = selectedPath[selectedPath.length - 1] + 1;
    onChange(insertStepAt(doc, parentPath, idx, newStep(addType)));
    selectStep([...parentPath, idx]);
  };

  const handleAddInside = () => {
    if (!selectedPath || !selectedStep) return;
    const end = listAt(doc, selectedPath).length;
    onChange(insertStepAt(doc, selectedPath, end, newStep(addType)));
    selectStep([...selectedPath, end]);
  };

  const handleMove = (delta: number) => {
    if (!selectedPath) return;
    const idx = selectedPath[selectedPath.length - 1];
    const next = moveStepAt(doc, selectedPath, delta);
    if (next === doc) return;
    onChange(next);
    selectStep([...parentPath, idx + delta]);
  };

  const handleDuplicate = () => {
    if (!selectedPath) return;
    onChange(duplicateStepAt(doc, selectedPath));
    selectStep([...parentPath, selectedPath[selectedPath.length - 1] + 1]);
  };

  const handleRemove = () => {
    if (!selectedPath) return;
    onChange(removeStepAt(doc, selectedPath));
    setSelectedKey(null);
  };

  const [wrapKind, setWrapKind] = useState<WrapKind>("while_match");
  const handleWrap = () => {
    if (!selectedPath) return;
    onChange(wrapStepAt(doc, selectedPath, wrapKind));
    // The wrapped step keeps its path; reselect the new container there.
    selectStep(selectedPath);
  };

  const [pasteNote, setPasteNote] = useState<string | null>(null);

  // Cmd/Ctrl+C copies the selected step as JSON; Cmd/Ctrl+V inserts the
  // clipboard step after the selection (or at the end of the root list).
  // Skipped while typing in inspector fields.
  useEffect(() => {
    const isEditableTarget = (t: EventTarget | null) => {
      const el = t as HTMLElement | null;
      if (!el) return false;
      const tag = el.tagName;
      return (
        tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable
      );
    };
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      if (isEditableTarget(e.target)) return;
      if (e.key === "c") {
        const step = selectedKey && selectedKey !== "start"
          ? getStepAt(doc, parsePathKey(selectedKey))
          : null;
        if (!step) return;
        e.preventDefault();
        void navigator.clipboard
          .writeText(JSON.stringify(step, null, 2))
          .then(() => setPasteNote("step copied"))
          .catch(() => setPasteNote("clipboard unavailable"));
      } else if (e.key === "v") {
        e.preventDefault();
        void navigator.clipboard
          .readText()
          .then((text) => {
            let step: unknown;
            try {
              step = JSON.parse(text);
            } catch {
              setPasteNote("clipboard is not a step (expected JSON)");
              return;
            }
            if (!step || typeof step !== "object" || Array.isArray(step)) {
              setPasteNote("clipboard is not a step (expected JSON)");
              return;
            }
            const path = selectedKey && selectedKey !== "start"
              ? parsePathKey(selectedKey)
              : null;
            const parent = path ? path.slice(0, -1) : [];
            const idx = path ? path[path.length - 1] + 1 : listAt(doc, []).length;
            onChange(insertStepAt(doc, parent, idx, step as Record<string, unknown>));
            selectStep([...parent, idx]);
            setPasteNote("step pasted");
          })
          .catch(() => setPasteNote("clipboard unavailable"));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [doc, selectedKey, onChange]);

  useEffect(() => {
    if (!pasteNote) return;
    const t = setTimeout(() => setPasteNote(null), 2500);
    return () => clearTimeout(t);
  }, [pasteNote]);

  const siblingCount = listAt(doc, parentPath).length;
  const selectedIdx = selectedPath ? selectedPath[selectedPath.length - 1] : -1;
  const selectedKind = selectedStep ? detectStepType(selectedStep) : "";
  const REGION_STEP_KINDS = [
    "click",
    "long_click",
    "match",
    "ocr",
    "while_match",
    "while_scroll",
  ];
  const selectedRegion =
    selectedStep && REGION_STEP_KINDS.includes(selectedKind)
      ? String(selectedStep[selectedKind] ?? "").trim() || null
      : null;

  const addRow = (
    <div className="mt-2 flex flex-wrap items-end gap-2 border-t pt-2" style={{ borderColor: "var(--wos-border)" }}>
      <AppListbox
        inline
        label="Step type"
        value={addType}
        onChange={setAddType}
        options={addTypes.map((t) => ({ value: t, label: t }))}
        minWidth={140}
      />
      <button type="button" className="btn-secondary" onClick={handleAddAfter}>
        {selectedStep ? "Add after" : "Add to end"}
      </button>
      {selectedStep && isContainerStep(selectedStep) ? (
        <button type="button" className="btn-secondary" onClick={handleAddInside}>
          Add inside
        </button>
      ) : null}
      {selectedStep ? (
        <span className="flex items-end gap-2">
          <AppListbox
            inline
            label="Wrap in"
            value={wrapKind}
            onChange={(v) => setWrapKind(v as WrapKind)}
            options={WRAP_KINDS.map((k) => ({ value: k, label: k }))}
            minWidth={130}
          />
          <button
            type="button"
            className="btn-secondary"
            onClick={handleWrap}
            {...tip("Wrap this step in a new container")}
          >
            Wrap
          </button>
        </span>
      ) : null}
      <span className="muted w-full text-xs">
        {pasteNote ?? "⌘C / ⌘V — copy & paste steps as JSON"}
      </span>
    </div>
  );

  const inspector =
    selectedStep && selectedPath ? (
      <>
        <div className="mb-2 flex items-center gap-2">
          <code className="text-sm font-semibold">{selectedKind}</code>
          <span className="muted text-xs">step {selectedPath.map((i) => i + 1).join(" → ")}</span>
          <span className="ml-auto flex items-center gap-1">
            <button
              type="button"
              className="btn-icon"
              disabled={selectedIdx <= 0}
              onClick={() => handleMove(-1)}
              aria-label="Move up"
              {...tip("Move up")}
            >
              <Icon name="arrow-up" size="sm" />
            </button>
            <button
              type="button"
              className="btn-icon"
              disabled={selectedIdx >= siblingCount - 1}
              onClick={() => handleMove(1)}
              aria-label="Move down"
              {...tip("Move down")}
            >
              <Icon name="arrow-down" size="sm" />
            </button>
            <button
              type="button"
              className="btn-icon"
              onClick={handleDuplicate}
              aria-label="Duplicate step"
              {...tip("Duplicate")}
            >
              <Icon name="copy" size="sm" />
            </button>
            <button
              type="button"
              className="btn-icon"
              onClick={handleRemove}
              aria-label="Remove step"
              {...tip(isContainerStep(selectedStep) ? "Remove (with inner steps)" : "Remove")}
            >
              <Icon name="trash" size="sm" />
            </button>
          </span>
        </div>
        <StepCard
          step={selectedStep}
          path={selectedPath}
          meta={meta}
          onChange={(s) => onChange(updateStepAt(doc, selectedPath, s))}
        />
        {selectedRegion ? (
          <>
            <RegionScreenPreview region={selectedRegion} meta={meta} />
            <RegionTestPanel
              region={selectedRegion}
              kind={selectedKind}
              threshold={
                Number(selectedStep.threshold) > 0
                  ? Number(selectedStep.threshold)
                  : null
              }
            />
          </>
        ) : null}
        {addRow}
      </>
    ) : (
      <>
        <p className="muted mt-0 text-xs">
          Click a node to edit it here. Scenario header:
        </p>
        <ScenarioHeaderForm
          doc={doc}
          rel={rel}
          meta={meta}
          collisions={collisions}
          onCollisionsChange={onCollisionsChange}
          onChange={onChange}
        />
        <CallersPanel rel={rel} />
        {addRow}
      </>
    );

  return (
    <div className="flex flex-col gap-3 xl:flex-row">
      <div
        className="panel min-w-0 flex-1"
        style={{ height: CANVAS_H, padding: 0, overflow: "hidden" }}
      >
        <ReactFlow
          nodes={rfNodes}
          edges={edges}
          nodeTypes={nodeTypes}
          colorMode={theme}
          fitView
          fitViewOptions={{ maxZoom: 1 }}
          minZoom={0.1}
          nodesConnectable={false}
          nodesDraggable={false}
          edgesFocusable={false}
          onNodeClick={(_, node) =>
            setSelectedKey(
              node.id === START_NODE_ID
                ? "start"
                : String((node.data as { pathKey?: string }).pathKey ?? ""),
            )
          }
          onPaneClick={() => setSelectedKey(null)}
        >
          <Background />
          <Controls showInteractive={false} />
          {live || lastRun ? (
            <Panel position="top-left">
              <div className="flex flex-col items-start gap-1">
                {live ? (
                  <span
                    className="flex items-center gap-2 rounded-lg border px-2 py-1 text-xs"
                    style={{
                      background:
                        "color-mix(in srgb, #22c55e 12%, var(--wos-panel-raised))",
                      borderColor: "#22c55e88",
                    }}
                  >
                    <span className="h-2 w-2 rounded-full" style={{ background: "#22c55e" }} />
                    running on <strong>{live.instanceId}</strong>
                    {live.step !== null ? <span>· step {live.step + 1}</span> : null}
                    {live.iter ? <span>· iter {live.iter}</span> : null}
                  </span>
                ) : null}
                {lastRun ? (
                  <span
                    className="flex items-center gap-2 rounded-lg border px-2 py-1 text-xs"
                    style={{
                      background: "var(--wos-panel-raised)",
                      borderColor: lastRun.success ? "#22c55e55" : "#ef444455",
                    }}
                  >
                    <span style={{ color: lastRun.success ? "#22c55e" : "#ef4444" }}>
                      {lastRun.success ? "✓" : "✗"}
                    </span>
                    last run {agoLabel(lastRun.finished_at)} on{" "}
                    <strong>{lastRun.instance_id}</strong>
                    <button
                      type="button"
                      className="region-labeling-link"
                      onClick={() => setShowTrace((v) => !v)}
                    >
                      {showTrace ? "hide" : "show"}
                    </button>
                  </span>
                ) : null}
              </div>
            </Panel>
          ) : null}
          <MiniMap
            pannable
            zoomable
            ariaLabel="Mini map"
            bgColor="var(--wos-surface)"
            maskColor="color-mix(in srgb, var(--wos-bg) 65%, transparent)"
            nodeStrokeColor="var(--wos-accent)"
            nodeStrokeWidth={3}
            nodeBorderRadius={4}
            nodeColor={(node) => {
              if (node.id === liveNodeId) return "#22c55e";
              if (node.id === selectedNodeId) return "#38bdf8";
              if (nodeIssues(node.data as Record<string, unknown>).length) return "#ef4444";
              const tr = nodeTrace(node.data as Record<string, unknown>);
              if (tr) return TRACE_COLORS[tr.bucket];
              return node.type === "dslContainer" ? "#475569" : "#7dd3fc";
            }}
          />
        </ReactFlow>
      </div>
      <aside
        className="panel w-full shrink-0 overflow-auto xl:w-[420px]"
        style={{ height: CANVAS_H, padding: 12 }}
      >
        {inspector}
      </aside>
    </div>
  );
}
