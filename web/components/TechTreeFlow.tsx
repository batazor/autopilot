"use client";

import { memo, useMemo, useState } from "react";
import Dagre from "@dagrejs/dagre";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Panel,
  Position,
  ReactFlow,
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

type TechNodeData = { node: FlowTreeNode; dir: Dir };

/** Custom React Flow node — icon chip + text, handles oriented by direction. */
const TechNode = memo(function TechNode({ data }: NodeProps) {
  const { node: n, dir } = data as TechNodeData;
  const targetPos = dir === "TB" ? Position.Top : Position.Left;
  const sourcePos = dir === "TB" ? Position.Bottom : Position.Right;
  return (
    <div
      className="flex items-center gap-2 rounded-lg border p-2 shadow-sm"
      style={{
        width: n.width ?? NODE_W,
        background: "var(--wos-panel-raised)",
        borderColor: "var(--wos-border)",
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

export function TechTreeFlow({
  nodes,
  height = 600,
  defaultDirection = "LR",
}: {
  nodes: FlowTreeNode[];
  height?: number;
  defaultDirection?: Dir;
}) {
  // Nodes carrying explicit positions (e.g. building swimlanes) opt out of
  // dagre and the direction toggle.
  const fixed = nodes.some((n) => n.position);
  const [dir, setDir] = useState<Dir>(defaultDirection);
  const effectiveDir: Dir = fixed ? "LR" : dir;

  const { rfNodes, rfEdges } = useMemo(() => {
    const base: Node[] = nodes.map((n) => ({
      id: n.id,
      type: "tech",
      position: n.position ?? { x: 0, y: 0 },
      data: { node: n, dir: effectiveDir },
      style: { width: n.width ?? NODE_W },
    }));
    const edges = buildEdges(nodes);
    return {
      rfNodes: fixed ? base : dagreLayout(base, edges, dir),
      rfEdges: edges,
    };
  }, [nodes, dir, fixed, effectiveDir]);

  return (
    <div className="panel" style={{ height, padding: 0, overflow: "hidden" }}>
      <ReactFlow
        // Remount on direction change so fitView re-frames the new layout.
        key={fixed ? "fixed" : dir}
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        colorMode="dark"
        fitView
        minZoom={0.2}
        nodesConnectable={false}
        edgesFocusable={false}
      >
        <Background />
        <Controls showInteractive={false} />
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
      </ReactFlow>
    </div>
  );
}
