import type { ReactNode } from "react";

type MetricGridProps = {
  /** Minimum column width for the auto-fit grid (default 8rem). */
  minColWidth?: string;
  className?: string;
  children: ReactNode;
};

/** Responsive auto-fit grid for {@link MetricCard} tiles. */
export function MetricGrid({ minColWidth = "8rem", className, children }: MetricGridProps) {
  return (
    <div
      className={["grid gap-3", className].filter(Boolean).join(" ")}
      style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${minColWidth}, 1fr))` }}
    >
      {children}
    </div>
  );
}
