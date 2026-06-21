/** Labelled value chip for the beta-registration automation state. */
export function AutomationChip({ label, value }: { label: string; value?: string }) {
  const clean = (value || "pending").replaceAll("_", " ");
  const state = (value || "").toLowerCase();
  const tone =
    state === "solved" || state === "dragged" || state === "awaiting_submit"
      ? "border-emerald-400/40 bg-emerald-500/15 text-emerald-200"
      : state === "failed" || state === "skipped"
        ? "border-amber-400/40 bg-amber-500/15 text-amber-200"
        : "border-wos-hairline bg-wos-surface/40 text-wos-text";
  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs ${tone}`}>
      <span className="muted mr-1">{label}</span>
      <strong className="font-semibold">{clean}</strong>
    </span>
  );
}
