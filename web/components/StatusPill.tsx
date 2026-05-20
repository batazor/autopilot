const STATUS_CLASS: Record<string, string> = {
  live: "pill-live",
  paused: "pill-paused",
  offline: "pill-offline",
  stale: "pill-stale",
  busy: "pill-busy",
  starting: "pill-stale",
  crashed: "pill-danger",
  restarting: "pill-danger",
};

export function StatusPill({ status }: { status: string }) {
  const key = status.toLowerCase();
  const cls = STATUS_CLASS[key] ?? "pill-offline";
  return <span className={`status-pill ${cls}`}>{status}</span>;
}
