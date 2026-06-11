"use client";

import { memo, useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
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
 *  are drawn prerequisite → this). `tier` is the column (1 = leftmost). */
export type FlowTreeNode = {
  id: string;
  tier: number;
  title: string;
  subtitle?: string;
  footer?: string;
  badge?: string;
  /** Emoji or short glyph shown in the node's icon chip. */
  icon?: string;
  requires: FlowRequire[];
};

const COL_W = 250;
const ROW_H = 108;
const NODE_W = 200;

function reqId(r: FlowRequire): string {
  return typeof r === "string" ? r : r.id;
}

type TechNodeData = { node: FlowTreeNode };

/** Custom React Flow node — icon chip + text, with left/right handles. */
const TechNode = memo(function TechNode({ data }: NodeProps) {
  const n = (data as TechNodeData).node;
  return (
    <div
      className="flex items-center gap-2 rounded-lg border p-2 shadow-sm"
      style={{
        width: NODE_W,
        background: "var(--wos-panel-raised)",
        borderColor: "var(--wos-border)",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
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
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
});

const nodeTypes = { tech: TechNode };

export function TechTreeFlow({
  nodes,
  height = 600,
}: {
  nodes: FlowTreeNode[];
  height?: number;
}) {
  const { rfNodes, rfEdges } = useMemo(() => {
    const rowOf = new Map<number, number>();
    const rfNodes: Node[] = nodes.map((n) => {
      const row = rowOf.get(n.tier) ?? 0;
      rowOf.set(n.tier, row + 1);
      return {
        id: n.id,
        type: "tech",
        position: { x: (n.tier - 1) * COL_W, y: row * ROW_H },
        data: { node: n },
        style: { width: NODE_W },
      };
    });

    const ids = new Set(nodes.map((n) => n.id));
    const rfEdges: Edge[] = nodes.flatMap((n) =>
      n.requires
        .filter((r) => ids.has(reqId(r)))
        .map((r) => {
          const src = reqId(r);
          const label = typeof r === "string" ? undefined : r.label;
          return {
            id: `${src}->${n.id}`,
            source: src,
            target: n.id,
            type: "smoothstep",
            label,
            markerEnd: { type: MarkerType.ArrowClosed },
          };
        }),
    );
    return { rfNodes, rfEdges };
  }, [nodes]);

  return (
    <div className="panel" style={{ height, padding: 0, overflow: "hidden" }}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        colorMode="dark"
        fitView
        minZoom={0.2}
        nodesConnectable={false}
        edgesFocusable={false}
        defaultEdgeOptions={{ type: "smoothstep" }}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
