"use client";

import { useEffect, useMemo, useState } from "react";
import { AppCheckbox } from "@/components/headless";
import {
  buildReferenceTree,
  pruneRefTree,
  referenceLeafTitle,
  refTreeGroupIdsForSelection,
  type RefTreeNode,
} from "@/lib/labeling-utils";
import type { LabelingReferenceMeta } from "@/lib/types";

type Props = {
  refs: LabelingReferenceMeta[];
  refRel: string;
  groupByScreenId: boolean;
  onGroupByScreenIdChange: (value: boolean) => void;
  onSelect: (rel: string) => void;
  disabled?: boolean;
};

function RefTreeBranch({
  nodes,
  depth,
  refRel,
  expanded,
  onToggle,
  onSelect,
  disabled,
}: {
  nodes: RefTreeNode[];
  depth: number;
  refRel: string;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onSelect: (rel: string) => void;
  disabled?: boolean;
}) {
  return (
    <ul
      className={depth === 0 ? "labeling-ref-tree" : "labeling-ref-tree__children"}
      role={depth === 0 ? "tree" : "group"}
    >
      {nodes.map((node) => {
        if (node.kind === "leaf") {
          const active = node.ref.rel === refRel;
          return (
            <li key={node.ref.rel} role="treeitem">
              <button
                type="button"
                className={
                  active
                    ? "labeling-ref-tree__leaf labeling-ref-tree__leaf--active"
                    : "labeling-ref-tree__leaf"
                }
                disabled={disabled}
                title={node.ref.rel}
                aria-current={active ? "true" : undefined}
                onClick={() => onSelect(node.ref.rel)}
              >
                {referenceLeafTitle(node.ref)}
              </button>
            </li>
          );
        }
        const open = expanded.has(node.id);
        return (
          <li key={node.id} role="treeitem" aria-expanded={open}>
            <button
              type="button"
              className="labeling-ref-tree__group-btn"
              disabled={disabled}
              aria-expanded={open}
              onClick={() => onToggle(node.id)}
            >
              <span className="labeling-ref-tree__caret" aria-hidden>
                {open ? "▾" : "▸"}
              </span>
              <span>{node.label}</span>
            </button>
            {open ? (
              <RefTreeBranch
                nodes={node.children}
                depth={depth + 1}
                refRel={refRel}
                expanded={expanded}
                onToggle={onToggle}
                onSelect={onSelect}
                disabled={disabled}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function LabelingReferenceTree({
  refs,
  refRel,
  groupByScreenId,
  onGroupByScreenIdChange,
  onSelect,
  disabled,
}: Props) {
  const tree = useMemo(() => {
    const built = buildReferenceTree(refs, groupByScreenId);
    return pruneRefTree(built);
  }, [refs, groupByScreenId]);

  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    const ids = refTreeGroupIdsForSelection(tree, refRel);
    if (!ids.length && tree.length) {
      const top = tree.filter((n) => n.kind === "group").map((n) => n.id);
      setExpanded(new Set(top));
      return;
    }
    setExpanded((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.add(id);
      return next;
    });
  }, [tree, refRel]);

  const onToggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="labeling-ref-tree-wrap">
      <AppCheckbox
        fieldClassName="labeling-toggle meta"
        checked={groupByScreenId}
        disabled={disabled}
        onChange={onGroupByScreenIdChange}
        label="Group by screen node"
      />

      {tree.length ? (
        <RefTreeBranch
          nodes={tree}
          depth={0}
          refRel={refRel}
          expanded={expanded}
          onToggle={onToggle}
          onSelect={onSelect}
          disabled={disabled}
        />
      ) : (
        <p className="meta labeling-ref-tree__empty">
          {refs.length ? "No references match the filter." : "No references in this module."}
        </p>
      )}
    </div>
  );
}
