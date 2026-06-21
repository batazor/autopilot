import type { ClickApprovalView } from "@/lib/types";

export function NavigationRoute({
  info,
}: {
  info: NonNullable<ClickApprovalView["navigation"]>;
}) {
  const { path, hop_index: hopIndex, from, to } = info;
  // When the worker provided a full BFS route + 1-based hop index we render
  // every node with the current edge highlighted — matches the Streamlit
  // "Navigation · `a` → **`b → c`** → `d`" formatting.
  if (path.length >= 2 && hopIndex >= 1 && hopIndex < path.length) {
    return (
      <p className="approvals-callout approvals-callout--warn">
        Navigation ·{" "}
        {path.map((node, i) => {
          const isCurrentEdge = i === hopIndex - 1 || i === hopIndex;
          const sep = i < path.length - 1 ? " → " : "";
          return (
            <span key={`${node}-${i}`}>
              {isCurrentEdge ? (
                <strong>
                  <code>{node}</code>
                </strong>
              ) : (
                <code>{node}</code>
              )}
              {sep}
            </span>
          );
        })}
      </p>
    );
  }
  if (from || to) {
    return (
      <p className="approvals-callout approvals-callout--warn">
        Navigation · <code>{from || "?"}</code> → <code>{to || "?"}</code>
      </p>
    );
  }
  return null;
}
