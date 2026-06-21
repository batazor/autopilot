import type { IconName } from "@/components/ui/Icon";
import type { NotificationEvent, ScenarioProgress } from "@/lib/types";

export function scenarioProgressLabel(progress: ScenarioProgress): string {
  if (progress.progress_label?.trim()) return progress.progress_label.trim();
  const key = progress.scenario_label || progress.scenario_key;
  if (progress.is_navigating && progress.nav_target) {
    return `${key} · Navigating → ${progress.nav_target}`;
  }
  if (key && progress.step_total > 0) {
    let text = `${key} · Step ${progress.step_current + 1}/${progress.step_total}`;
    if (progress.is_running && progress.step_iter > 0) {
      text += ` · iter ${progress.step_iter}`;
    }
    if (!progress.is_running) text += " · idle";
    return text;
  }
  if (key) return `${key} · running`;
  return "no active scenario";
}

export function toastLevelIcon(level: NotificationEvent["level"]): IconName {
  switch (level) {
    case "success":
      return "check";
    case "warning":
      return "warning";
    case "error":
      return "alert";
    default:
      return "info";
  }
}

export function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function fmtMaybe(value: unknown): string {
  const n = asNumber(value);
  return n == null ? "—" : n.toFixed(4);
}

export function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1, value));
}
