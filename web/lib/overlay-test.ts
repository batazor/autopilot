/** Overlay analyzer actions (matches Streamlit idle overlay probe). */
export const OVERLAY_ACTION_TYPES = [
  "findIcon",
  "color_check",
  "text",
  "red_dot",
  "tab_active",
  "white_border",
] as const;

export type OverlayActionType = (typeof OVERLAY_ACTION_TYPES)[number];

export type MatchStatusFilter = "all" | "matched" | "unmatched";

export function overlayLabelRuleName(label: string | undefined): string | null {
  if (!label) return null;
  if (label.startsWith("search:")) return null;
  return label.replace(/\s+[✓✗]$/, "").trim() || null;
}

export function defaultActionVisibility(): Record<string, boolean> {
  return Object.fromEntries(OVERLAY_ACTION_TYPES.map((a) => [a, true]));
}
