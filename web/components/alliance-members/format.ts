/** Compact power formatting: 80_600_000 → "80.6M", 1_400_000_000 → "1.40B". */
export function fmtPower(n: number): string {
  if (!n) return "0";
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/** Compact relative last-active label derived from the parsed seconds, e.g.
 *  "Online" / "5h ago" / "2d ago". Falls back to "—" when the time couldn't be
 *  read (rather than echoing garbled OCR like "hoursaga"). */
export function lastActiveLabel(m: {
  online: boolean;
  last_online_seconds: number | null;
}): string {
  if (m.online) return "Online";
  const s = m.last_online_seconds;
  if (s == null) return "—";
  if (s < 60) return `${s}s ago`;
  if (s < 3_600) return `${Math.round(s / 60)}m ago`;
  if (s < 86_400) return `${Math.round(s / 3_600)}h ago`;
  if (s < 604_800) return `${Math.round(s / 86_400)}d ago`;
  return `${Math.round(s / 604_800)}w ago`;
}
