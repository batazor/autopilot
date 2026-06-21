"use client";

import type { ReactNode } from "react";

type Props = {
  title: ReactNode;
  /** Shown in the summary, e.g. filtered/total counts. */
  meta?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
};

export function CollapsiblePanel({
  title,
  meta,
  defaultOpen = true,
  children,
  className = "",
}: Props) {
  return (
    <details
      className={`panel player-state-section ${className}`.trim()}
      open={defaultOpen}
    >
      <summary className="player-state-section__summary">
        <span className="player-state-section__title">{title}</span>
        {meta ? <span className="player-state-section__meta meta">{meta}</span> : null}
      </summary>
      <div className="player-state-section__body">{children}</div>
    </details>
  );
}
