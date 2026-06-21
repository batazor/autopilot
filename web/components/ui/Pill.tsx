import type { ReactNode } from "react";

export type PillTone =
  | "live"
  | "paused"
  | "offline"
  | "stale"
  | "danger"
  | "busy"
  | "ok"
  | "pending"
  | "neutral";

const TONE_CLASS: Record<PillTone, string> = {
  live: "pill-live",
  paused: "pill-paused",
  offline: "pill-offline",
  stale: "pill-stale",
  danger: "pill-danger",
  busy: "pill-busy",
  ok: "status-idle",
  pending: "status-pending",
  neutral: "pill-offline",
};

type PillProps = {
  /** Semantic colour; maps to the shared `.pill-*` / `.status-*` classes. */
  tone?: PillTone;
  /** Render the leading status dot. */
  dot?: boolean;
  /** Larger padding/size variant. */
  size?: "md" | "lg";
  /** Pulse animation (e.g. a fresh approval request). */
  pulse?: boolean;
  /** Extra classes appended after the tone class. */
  className?: string;
  title?: string;
  children: ReactNode;
};

/**
 * Status-pill primitive wrapping the shared `.status-pill` design-system class.
 * Use {@link StatusPill} for the fleet-status string convenience.
 */
export function Pill({
  tone = "neutral",
  dot = false,
  size = "md",
  pulse = false,
  className,
  title,
  children,
}: PillProps) {
  return (
    <span
      className={[
        "status-pill",
        TONE_CLASS[tone],
        size === "lg" ? "status-pill--lg" : "",
        pulse ? "pulse" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      title={title}
    >
      {dot ? <span className="status-pill__dot" aria-hidden /> : null}
      {children}
    </span>
  );
}
