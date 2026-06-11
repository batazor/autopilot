"use client";

import { memo, useMemo, useState, type ReactNode } from "react";
import Dagre from "@dagrejs/dagre";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

/** A prerequisite edge: id of the required node, plus an optional edge label. */
export type FlowRequire = string | { id: string; label?: string };

/** A dependency-graph node: `requires` are ids of prerequisite nodes (edges
 *  are drawn prerequisite → this). */
export type FlowTreeNode = {
  id: string;
  tier: number;
  title: string;
  subtitle?: string;
  footer?: string;
  badge?: string;
  /** Emoji or short glyph shown in the node's icon chip. */
  icon?: string;
  /** Explicit canvas position; when set, overrides auto (dagre) layout. */
  position?: { x: number; y: number };
  /** Card width in px (default 200). */
  width?: number;
  requires: FlowRequire[];
};

/** Layout direction: top→bottom (vertical) or left→right (horizontal). */
type Dir = "TB" | "LR";

const NODE_W = 200;
const NODE_H = 64;

function reqId(r: FlowRequire): string {
  return typeof r === "string" ? r : r.id;
}

type TechNodeData = {
  node: FlowTreeNode;
  dir: Dir;
  dim: boolean;
  selected: boolean;
};

/** Custom React Flow node — icon chip + text, handles oriented by direction. */
const TechNode = memo(function TechNode({ data }: NodeProps) {
  const { node: n, dir, dim, selected } = data as TechNodeData;
  const targetPos = dir === "TB" ? Position.Top : Position.Left;
  const sourcePos = dir === "TB" ? Position.Bottom : Position.Right;
  return (
    <div
      className="flex items-center gap-2 rounded-lg border p-2 shadow-sm transition-opacity"
      style={{
        width: n.width ?? NODE_W,
        background: "var(--wos-panel-raised)",
        borderColor: selected ? "var(--wos-accent)" : "var(--wos-border)",
        boxShadow: selected ? "0 0 0 2px var(--wos-accent)" : undefined,
        opacity: dim ? 0.2 : 1,
      }}
    >
      <Handle type="target" position={targetPos} style={{ opacity: 0 }} />
      {n.icon ? (
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-lg"
          style={{ background: "var(--wos-surface)" }}
          aria-hidden
        >
          {n.icon}
        </span>
      ) : null}
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-1.5">
          <span className="truncate text-sm font-medium" title={n.title}>
            {n.title}
          </span>
          {n.badge ? (
            <span
              className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold"
              style={{
                background: "var(--wos-status-info-bg)",
                color: "var(--wos-status-info-fg)",
              }}
            >
              {n.badge}
            </span>
          ) : null}
        </div>
        {n.subtitle ? (
          <div className="truncate text-xs text-wos-text-muted" title={n.subtitle}>
            {n.subtitle}
          </div>
        ) : null}
        {n.footer ? (
          <div className="text-[11px] text-wos-text-secondary">{n.footer}</div>
        ) : null}
      </div>
      <Handle type="source" position={sourcePos} style={{ opacity: 0 }} />
    </div>
  );
});

const nodeTypes = { tech: TechNode };

function buildEdges(nodes: FlowTreeNode[]): Edge[] {
  const ids = new Set(nodes.map((n) => n.id));
  return nodes.flatMap((n) =>
    n.requires
      .filter((r) => ids.has(reqId(r)))
      .map((r) => {
        const src = reqId(r);
        return {
          id: `${src}->${n.id}`,
          source: src,
          target: n.id,
          type: "smoothstep",
          label: typeof r === "string" ? undefined : r.label,
          markerEnd: { type: MarkerType.ArrowClosed },
        };
      }),
  );
}

/** Dagre auto-layout (https://reactflow.dev/examples/layout/dagre). */
function dagreLayout(rfNodes: Node[], edges: Edge[], dir: Dir): Node[] {
  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: dir, nodesep: 40, ranksep: 80 });
  for (const n of rfNodes) {
    const w = (n.style?.width as number) ?? NODE_W;
    g.setNode(n.id, { width: w, height: NODE_H });
  }
  for (const e of edges) g.setEdge(e.source, e.target);
  Dagre.layout(g);
  return rfNodes.map((n) => {
    const { x, y } = g.node(n.id);
    const w = (n.style?.width as number) ?? NODE_W;
    return { ...n, position: { x: x - w / 2, y: y - NODE_H / 2 } };
  });
}

/** Transitive prerequisites + dependents of `id` (and `id` itself). */
function relatedSet(id: string, edges: Edge[]): Set<string> {
  const prereqs = new Map<string, string[]>();
  const dependents = new Map<string, string[]>();
  for (const e of edges) {
    (prereqs.get(e.target) ?? prereqs.set(e.target, []).get(e.target)!).push(e.source);
    (dependents.get(e.source) ?? dependents.set(e.source, []).get(e.source)!).push(
      e.target,
    );
  }
  const out = new Set<string>([id]);
  const walk = (start: string, adj: Map<string, string[]>) => {
    const stack = [start];
    while (stack.length) {
      const cur = stack.pop()!;
      for (const next of adj.get(cur) ?? []) {
        if (!out.has(next)) {
          out.add(next);
          stack.push(next);
        }
      }
    }
  };
  walk(id, prereqs);
  walk(id, dependents);
  return out;
}

/** Search box (inside the flow) that centers + selects a matching node. */
function SearchPanel({
  nodes,
  onPick,
}: {
  nodes: FlowTreeNode[];
  onPick: (id: string) => void;
}) {
  const rf = useReactFlow();
  const [q, setQ] = useState("");
  const matches = q.trim()
    ? nodes
        .filter((n) => n.title.toLowerCase().includes(q.trim().toLowerCase()))
        .slice(0, 8)
    : [];

  const focus = (id: string) => {
    const n = rf.getNode(id);
    if (n) {
      const w = (n.width ?? (n.style?.width as number) ?? NODE_W) as number;
      rf.setCenter(n.position.x + w / 2, n.position.y + NODE_H / 2, {
        zoom: 1.3,
        duration: 400,
      });
    }
    onPick(id);
    setQ("");
  };

  return (
    <Panel position="top-left">
      <div
        className="rounded-lg border p-1.5 shadow"
        style={{
          background: "var(--wos-panel-raised)",
          borderColor: "var(--wos-border)",
        }}
      >
        <input
          type="search"
          placeholder="Search…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="w-40 rounded bg-transparent px-1.5 py-1 text-sm outline-none"
        />
        {matches.length ? (
          <ul className="mt-1 max-h-48 overflow-auto text-sm">
            {matches.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left hover:bg-[color:var(--wos-option-hover)]"
                  onClick={() => focus(m.id)}
                >
                  {m.icon ? <span>{m.icon}</span> : null}
                  <span className="truncate">
                    {m.title}
                    {m.badge ? ` · ${m.badge}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Panel>
  );
}

export function TechTreeFlow({
  nodes,
  height = 600,
  defaultDirection = "LR",
  renderDetail,
}: {
  nodes: FlowTreeNode[];
  height?: number;
  defaultDirection?: Dir;
  /** Detail content for the selected node id; shown in a side panel. */
  renderDetail?: (id: string) => ReactNode;
}) {
  const fixed = nodes.some((n) => n.position);
  const [dir, setDir] = useState<Dir>(defaultDirection);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const effectiveDir: Dir = fixed ? "LR" : dir;

  // Layout (dagre) is recomputed only when the graph or direction changes —
  // hover/selection just restyle, so they never re-run the layout.
  const { layoutNodes, edges } = useMemo(() => {
    const base: Node[] = nodes.map((n) => ({
      id: n.id,
      type: "tech",
      position: n.position ?? { x: 0, y: 0 },
      data: { node: n },
      style: { width: n.width ?? NODE_W },
    }));
    const e = buildEdges(nodes);
    return { layoutNodes: fixed ? base : dagreLayout(base, e, dir), edges: e };
  }, [nodes, dir, fixed]);

  const activeId = hoverId ?? selectedId;
  const related = useMemo(
    () => (activeId ? relatedSet(activeId, edges) : null),
    [activeId, edges],
  );

  const rfNodes = useMemo(
    () =>
      layoutNodes.map((n) => ({
        ...n,
        data: {
          node: (n.data as { node: FlowTreeNode }).node,
          dir: effectiveDir,
          dim: related ? !related.has(n.id) : false,
          selected: n.id === selectedId,
        },
      })),
    [layoutNodes, effectiveDir, related, selectedId],
  );

  const rfEdges = useMemo(
    () =>
      edges.map((e) => {
        const on = related ? related.has(e.source) && related.has(e.target) : true;
        return {
          ...e,
          animated: Boolean(related) && on,
          style: related
            ? {
                opacity: on ? 1 : 0.1,
                stroke: on ? "var(--wos-accent)" : undefined,
              }
            : undefined,
        };
      }),
    [edges, related],
  );

  return (
    <div className="panel" style={{ height, padding: 0, overflow: "hidden" }}>
      <ReactFlow
        key={fixed ? "fixed" : dir}
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        colorMode="dark"
        fitView
        minZoom={0.2}
        nodesConnectable={false}
        edgesFocusable={false}
        onNodeMouseEnter={(_, node) => setHoverId(node.id)}
        onNodeMouseLeave={() => setHoverId(null)}
        onNodeClick={(_, node) => setSelectedId(node.id)}
        onPaneClick={() => setSelectedId(null)}
      >
        <Background />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable />
        <SearchPanel nodes={nodes} onPick={setSelectedId} />
        {!fixed ? (
          <Panel position="top-right">
            <div className="flex gap-1">
              <button
                type="button"
                className={dir === "TB" ? "btn-primary" : "btn-secondary"}
                onClick={() => setDir("TB")}
              >
                Vertical
              </button>
              <button
                type="button"
                className={dir === "LR" ? "btn-primary" : "btn-secondary"}
                onClick={() => setDir("LR")}
              >
                Horizontal
              </button>
            </div>
          </Panel>
        ) : null}
        {renderDetail && selectedId ? (
          <Panel position="top-left">
            <div
              className="max-w-xs rounded-lg border p-3 text-sm shadow-lg"
              style={{
                background: "var(--wos-panel-raised)",
                borderColor: "var(--wos-border)",
              }}
            >
              <div className="mb-2 flex justify-end">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setSelectedId(null)}
                >
                  Close
                </button>
              </div>
              {renderDetail(selectedId)}
            </div>
          </Panel>
        ) : null}
      </ReactFlow>
    </div>
  );
}
