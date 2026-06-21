"use client";

import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Dagre from "@dagrejs/dagre";
import { toPng } from "html-to-image";
import {
  Background,
  Controls,
  getNodesBounds,
  getViewportForBounds,
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
import { useTheme } from "@/components/ThemeProvider";

/** A prerequisite edge: id of the required node, plus an optional edge label. */
export type FlowRequire = string | { id: string; label?: string };

/** Edge-flow direction for a node's handles (prerequisite → dependent). */
export type FlowDir = "TB" | "BT" | "LR" | "RL";

/** A dependency-graph node: `requires` are ids of prerequisite nodes (edges
 *  are drawn prerequisite → this). */
export type FlowTreeNode = {
  id: string;
  tier: number;
  title: string;
  subtitle?: string;
  footer?: string;
  badge?: string;
  /** Emoji/short glyph, or an image path (starts with "/") for the icon chip. */
  icon?: string;
  /** Player-progress overlay: node fully completed. */
  done?: boolean;
  /** Explicit canvas position; when set, overrides auto (dagre) layout. */
  position?: { x: number; y: number };
  /** Per-node edge-flow direction (for fixed multi-direction layouts). */
  dir?: FlowDir;
  /** Card width in px (default 200). */
  width?: number;
  requires: FlowRequire[];
};

/** Layout direction: top→bottom (vertical) or left→right (horizontal). */
type Dir = "TB" | "LR";

export const NODE_W = 200;
export const NODE_H = 64;

const HANDLE_POS: Record<FlowDir, [Position, Position]> = {
  TB: [Position.Top, Position.Bottom],
  BT: [Position.Bottom, Position.Top],
  LR: [Position.Left, Position.Right],
  RL: [Position.Right, Position.Left],
};

function reqId(r: FlowRequire): string {
  return typeof r === "string" ? r : r.id;
}

/** Dagre layout over plain FlowTreeNodes → top-left position per node id.
 *  Lets callers compose multi-direction fixed layouts (`position` + `dir`). */
export function flowLayout(
  nodes: FlowTreeNode[],
  rankdir: FlowDir,
): Map<string, { x: number; y: number }> {
  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir, nodesep: 40, ranksep: 80 });
  for (const n of nodes) g.setNode(n.id, { width: n.width ?? NODE_W, height: NODE_H });
  for (const n of nodes)
    for (const r of n.requires) {
      const src = reqId(r);
      if (g.hasNode(src)) g.setEdge(src, n.id);
    }
  Dagre.layout(g);
  return new Map(
    nodes.map((n) => {
      const { x, y } = g.node(n.id);
      const w = n.width ?? NODE_W;
      return [n.id, { x: x - w / 2, y: y - NODE_H / 2 }];
    }),
  );
}

type TechNodeData = {
  node: FlowTreeNode;
  dir: Dir;
  dim: boolean;
  selected: boolean;
};

/** Custom React Flow node — icon chip + text, handles oriented by direction.
 *  Memoized on the data fields (not object identity) so hover/selection only
 *  re-renders the nodes whose dim/selected state actually changed. */
const TechNode = memo(function TechNode({ data }: NodeProps) {
  const { node: n, dir, dim, selected } = data as TechNodeData;
  const [targetPos, sourcePos] = HANDLE_POS[n.dir ?? dir];
  return (
    <div
      className="flex items-center gap-2 rounded-lg border p-2 shadow-sm transition-opacity"
      style={{
        width: n.width ?? NODE_W,
        background: n.done
          ? "color-mix(in srgb, #22c55e 12%, var(--wos-panel-raised))"
          : "var(--wos-panel-raised)",
        borderColor: selected
          ? "var(--wos-accent)"
          : n.done
            ? "#22c55e88"
            : "var(--wos-border)",
        boxShadow: selected ? "0 0 0 2px var(--wos-accent)" : undefined,
        opacity: dim ? 0.2 : 1,
      }}
    >
      <Handle type="target" position={targetPos} style={{ opacity: 0 }} />
      {n.icon ? (
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-md text-lg"
          style={{ background: "var(--wos-surface)" }}
          aria-hidden
        >
          {n.icon.startsWith("/") ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={n.icon} alt="" className="h-9 w-9 object-contain" />
          ) : (
            n.icon
          )}
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
}, (prev, next) => {
  const a = prev.data as TechNodeData;
  const b = next.data as TechNodeData;
  return (
    a.node === b.node && a.dir === b.dir && a.dim === b.dim && a.selected === b.selected
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

/** Export the whole graph (all nodes, not just the viewport) to a PNG. */
function DownloadButton({ name }: { name: string }) {
  const { getNodes } = useReactFlow();
  const onClick = () => {
    const viewport = document.querySelector<HTMLElement>(".react-flow__viewport");
    if (!viewport) return;
    const bounds = getNodesBounds(getNodes());
    const pad = 48;
    const width = Math.min(8000, Math.ceil(bounds.width) + pad * 2);
    const height = Math.min(8000, Math.ceil(bounds.height) + pad * 2);
    const vp = getViewportForBounds(bounds, width, height, 0.2, 2, pad);
    const bg =
      getComputedStyle(document.documentElement)
        .getPropertyValue("--wos-bg")
        .trim() || "#1c2433";
    void toPng(viewport, {
      backgroundColor: bg,
      width,
      height,
      style: {
        width: `${width}px`,
        height: `${height}px`,
        transform: `translate(${vp.x}px, ${vp.y}px) scale(${vp.zoom})`,
      },
    }).then((dataUrl) => {
      const a = document.createElement("a");
      a.href = dataUrl;
      a.download = `${name}.png`;
      a.click();
    });
  };
  return (
    <button type="button" className="btn-secondary" onClick={onClick}>
      Export PNG
    </button>
  );
}

export function TechTreeFlow({
  nodes,
  height = 600,
  defaultDirection = "LR",
  renderDetail,
  exportName = "graph",
}: {
  nodes: FlowTreeNode[];
  height?: number;
  defaultDirection?: Dir;
  /** Detail content for the selected node id; shown in a side panel. */
  renderDetail?: (id: string) => ReactNode;
  /** Base filename for the PNG export. */
  exportName?: string;
}) {
  const { theme } = useTheme();
  const fixed = nodes.some((n) => n.position);
  const [dir, setDir] = useState<Dir>(defaultDirection);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const effectiveDir: Dir = fixed ? defaultDirection : dir;

  // Clearing hover is slightly delayed so cursor jitter at a node's border
  // (leave → enter within a few ms) doesn't pulse the whole-graph dimming.
  const hoverClearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hoverEnter = (id: string) => {
    if (hoverClearTimer.current) {
      clearTimeout(hoverClearTimer.current);
      hoverClearTimer.current = null;
    }
    setHoverId(id);
  };
  const hoverLeave = () => {
    if (hoverClearTimer.current) clearTimeout(hoverClearTimer.current);
    hoverClearTimer.current = setTimeout(() => setHoverId(null), 120);
  };
  useEffect(
    () => () => {
      if (hoverClearTimer.current) clearTimeout(hoverClearTimer.current);
    },
    [],
  );

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

  // Graph highlight previews whatever is under the cursor; the detail card is
  // pinned to the clicked node and only follows hover while nothing is pinned.
  const activeId = hoverId ?? selectedId;
  const detailId = selectedId ?? hoverId;
  const related = useMemo(() => {
    if (!activeId) return null;
    const set = relatedSet(activeId, edges);
    // An isolated node (no edges, e.g. a decorative hub) shouldn't dim the
    // rest of the graph.
    return set.size > 1 ? set : null;
  }, [activeId, edges]);

  // React Flow skips unchanged nodes/edges by object identity, so reuse the
  // previous objects for items whose styling didn't change — a hover then only
  // re-renders the nodes that actually dim/undim instead of the whole graph.
  const prevNodes = useRef(new Map<string, Node>());
  const rfNodes = useMemo(() => {
    const next = new Map<string, Node>();
    const out = layoutNodes.map((base) => {
      const data: TechNodeData = {
        node: (base.data as { node: FlowTreeNode }).node,
        dir: effectiveDir,
        dim: related ? !related.has(base.id) : false,
        selected: base.id === selectedId,
      };
      const prev = prevNodes.current.get(base.id);
      const pd = prev?.data as TechNodeData | undefined;
      const keep =
        prev &&
        prev.position === base.position &&
        pd!.node === data.node &&
        pd!.dir === data.dir &&
        pd!.dim === data.dim &&
        pd!.selected === data.selected;
      const node = keep ? prev : { ...base, data };
      next.set(base.id, node);
      return node;
    });
    prevNodes.current = next;
    return out;
  }, [layoutNodes, effectiveDir, related, selectedId]);

  const prevEdges = useRef(new Map<string, Edge>());
  const rfEdges = useMemo(() => {
    const next = new Map<string, Edge>();
    const out = edges.map((e) => {
      const on = related ? related.has(e.source) && related.has(e.target) : null;
      const prev = prevEdges.current.get(e.id);
      const prevOn = prev ? ((prev as Edge & { __on?: boolean | null }).__on ?? null) : undefined;
      const keep = prev && prevOn === on;
      const edge = keep
        ? prev
        : Object.assign(
            {
              ...e,
              style:
                on === null
                  ? undefined
                  : {
                      opacity: on ? 1 : 0.12,
                      stroke: on ? "var(--wos-accent)" : undefined,
                      strokeWidth: on ? 2 : undefined,
                    },
            },
            { __on: on },
          );
      next.set(e.id, edge);
      return edge;
    });
    prevEdges.current = next;
    return out;
  }, [edges, related]);

  return (
    <div className="flex flex-col gap-3 lg:flex-row">
      <div
        className="panel min-w-0 flex-1"
        style={{ height, padding: 0, overflow: "hidden" }}
      >
        <ReactFlow
        key={fixed ? "fixed" : dir}
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        colorMode={theme}
        fitView
        minZoom={0.05}
        nodesConnectable={false}
        edgesFocusable={false}
        onNodeMouseEnter={(_, node) => hoverEnter(node.id)}
        onNodeMouseLeave={hoverLeave}
        onNodeClick={(_, node) => setSelectedId(node.id)}
        onPaneClick={() => setSelectedId(null)}
      >
        <Background />
        <Controls showInteractive={false} />
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
            const d = node.data as Partial<TechNodeData>;
            if (d.selected) return "#38bdf8"; // sky-400
            if (d.dim) return "#475569"; // slate-600 (out-of-path)
            return "#7dd3fc"; // sky-300
          }}
        />
        <SearchPanel nodes={nodes} onPick={setSelectedId} />
        <Panel position="top-right">
          <div className="flex gap-1">
            {!fixed ? (
              <>
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
              </>
            ) : null}
            <DownloadButton name={exportName} />
          </div>
        </Panel>
        </ReactFlow>
      </div>
      {renderDetail ? (
        <aside
          className="panel w-full shrink-0 overflow-auto lg:w-[420px]"
          style={{ height, padding: 12 }}
        >
          {detailId ? (
            <div className="text-sm">
              {selectedId ? (
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs text-wos-text-muted">
                    Pinned — click the graph background to unpin
                  </span>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setSelectedId(null)}
                  >
                    Close
                  </button>
                </div>
              ) : null}
              {renderDetail(detailId)}
            </div>
          ) : (
            <p className="muted m-0 text-sm">
              Hover a node to see its details here. Click a node to pin them.
            </p>
          )}
        </aside>
      ) : null}
    </div>
  );
}
