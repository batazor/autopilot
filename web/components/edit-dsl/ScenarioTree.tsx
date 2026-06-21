"use client";

import dynamic from "next/dynamic";
import type { ReactElement } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { NodeRendererProps } from "react-arborist";
import type { ScenarioTreeNode } from "@/lib/config-pages";

const Tree = dynamic(
  () => import("react-arborist").then((mod) => mod.Tree),
  { ssr: false, loading: () => <p className="muted">Loading tree…</p> },
) as unknown as <T>(props: TreeProps<T>) => ReactElement;

// Minimal subset of react-arborist's TreeProps that we need.
type TreeProps<T> = {
  data: readonly T[];
  idAccessor?: string | ((d: T) => string);
  childrenAccessor?: string | ((d: T) => readonly T[] | null);
  selection?: string;
  initialOpenState?: Record<string, boolean>;
  openByDefault?: boolean;
  rowHeight?: number;
  indent?: number;
  width?: number | string;
  height?: number;
  disableMultiSelection?: boolean;
  disableDrag?: boolean;
  disableDrop?: boolean;
  disableEdit?: boolean;
  onActivate?: (node: { data: T; isLeaf: boolean }) => void;
  children?: (props: NodeRendererProps<T>) => ReactElement;
};

type Props = {
  nodes: ScenarioTreeNode[];
  selected: string;
  onSelect: (rel: string) => void;
  height?: number;
};

/** Build the set of ancestor ids leading to `selected` so the tree opens them. */
function ancestorOpenState(
  nodes: ScenarioTreeNode[],
  selected: string,
): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  const walk = (list: ScenarioTreeNode[], trail: string[]): boolean => {
    for (const n of list) {
      if (n.value === selected) {
        for (const id of trail) out[id] = true;
        return true;
      }
      if (n.children && walk(n.children, [...trail, n.value])) return true;
    }
    return false;
  };
  walk(nodes, []);
  return out;
}

function ScenarioTreeNodeRow({
  node,
  style,
  dragHandle,
}: NodeRendererProps<ScenarioTreeNode>) {
  const data = node.data;
  const isDir = data.is_dir;
  const active = node.isSelected;
  const cls = [
    "scenario-tree-row",
    isDir ? "scenario-tree-row--dir" : "scenario-tree-row--file",
    active ? "active" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div
      ref={dragHandle}
      style={style}
      className={cls}
      onClick={() => {
        if (isDir) node.toggle();
        else node.select();
      }}
    >
      <span className="scenario-tree-row__caret" aria-hidden>
        {isDir ? (node.isOpen ? "▾" : "▸") : ""}
      </span>
      <span className="scenario-tree-row__title">{data.title}</span>
    </div>
  );
}

export function ScenarioTree({
  nodes,
  selected,
  onSelect,
  height = 520,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState<number>(280);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && Math.abs(w - width) > 1) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [width]);

  const openState = useMemo(
    () => ancestorOpenState(nodes, selected),
    [nodes, selected],
  );

  return (
    <div ref={containerRef} className="scenario-tree-wrap">
      <Tree<ScenarioTreeNode>
        data={nodes}
        idAccessor="value"
        childrenAccessor="children"
        selection={selected || undefined}
        initialOpenState={openState}
        openByDefault={false}
        rowHeight={26}
        indent={16}
        width={width}
        height={height}
        disableDrag
        disableDrop
        disableEdit
        disableMultiSelection
        onActivate={(node) => {
          if (!node.data.is_dir) onSelect(node.data.value);
        }}
      >
        {ScenarioTreeNodeRow}
      </Tree>
    </div>
  );
}
