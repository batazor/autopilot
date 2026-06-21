import { Pill, type PillTone } from "./Pill";

const STATUS_TONE: Record<string, PillTone> = {
  live: "live",
  paused: "paused",
  offline: "offline",
  stale: "stale",
  busy: "busy",
  starting: "stale",
  crashed: "danger",
  restarting: "danger",
};

/**
 * Fleet-status convenience over {@link Pill}: maps a status string (live,
 * paused, stale, crashed, …) to the matching tone and renders the label.
 */
export function StatusPill({ status }: { status: string }) {
  const tone = STATUS_TONE[status.toLowerCase()] ?? "offline";
  return <Pill tone={tone}>{status}</Pill>;
}
