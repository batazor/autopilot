"use client";

import type { StreamStatus } from "@/lib/useDashboardEventStream";

const LABELS: Record<StreamStatus, string> = {
  live: "Live",
  connecting: "Connecting…",
  degraded: "Reconnecting…",
  paused: "Paused",
};

const TITLES: Record<StreamStatus, string> = {
  live: "Real-time updates streaming",
  connecting: "Opening the live update stream…",
  degraded: "Live stream dropped — falling back to periodic refresh while it reconnects",
  paused: "Live updates paused (tab in background)",
};

/** Small badge that surfaces the SSE stream health on a page. */
export function LiveIndicator({ status }: { status: StreamStatus }) {
  return (
    <span
      className={`live-indicator live-indicator--${status}`}
      role="status"
      aria-live="polite"
      title={TITLES[status]}
    >
      <span className="live-indicator__dot" aria-hidden />
      <span className="live-indicator__label">{LABELS[status]}</span>
    </span>
  );
}
