"use client";

import type { ReactNode } from "react";

type ChipProps = {
  /** Toggled (selected) state — drives the active styling. */
  active?: boolean;
  /** When provided, the chip renders as a `<button>` toggle; otherwise a `<span>`. */
  onClick?: () => void;
  className?: string;
  title?: string;
  children: ReactNode;
};

/**
 * Rounded pill-shaped chip used for filters / toggles. With `onClick` it is an
 * interactive toggle; without it, a static label.
 */
export function Chip({ active = false, onClick, className, title, children }: ChipProps) {
  const cls = [
    "rounded-full border px-2 py-0.5",
    active ? "border-wos-text/40 bg-wos-surface text-wos-text" : "border-wos-hairline muted",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={cls} title={title} aria-pressed={active}>
        {children}
      </button>
    );
  }
  return (
    <span className={cls} title={title}>
      {children}
    </span>
  );
}
