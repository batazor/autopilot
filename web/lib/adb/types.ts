import type { AdbDetectedGame, ScrcpyInstallResult } from "@/lib/config-pages";

export const INPUT_BACKEND_OPTIONS = [
  { value: "", label: "auto (scrcpy)" },
  { value: "adb", label: "adb" },
  { value: "scrcpy", label: "scrcpy" },
];

export const MANUAL_DEVICE_DEFAULT = {
  name: "",
  adb_serial: "",
  screenshot_backend: "",
  input_backend: "",
  replace_existing: false,
};

export const PORT_INPUT_CLASS = "field";

export const REGISTRATION_FILTER_OPTIONS = [
  { value: "", label: "All", title: "Show every device" },
  {
    value: "registered",
    label: "Registered",
    title: "Devices present in the fleet registry",
  },
  {
    value: "unregistered",
    label: "Unregistered",
    title: "Live devices missing from the fleet registry",
  },
];

export type RegistrationFilter = "" | "registered" | "unregistered";
export type AdbActivityTone = "info" | "success" | "error";
export type AdbActivityEntry = {
  at: string;
  tone: AdbActivityTone;
  label: string;
  detail?: string;
};

export type CellEntry<T> = T | { error: string } | undefined;

export function matchesQuery(
  query: string,
  fields: Array<string | null | undefined>,
): boolean {
  return fields.some((f) => f?.toLowerCase().includes(query));
}

export function scrcpyInstallNote(result?: ScrcpyInstallResult | null): string {
  if (!result) return "";
  if (result.ok || result.installed) return " Scrcpy server installed.";
  return ` Scrcpy auto-install failed: ${result.last_error ?? "unknown error"}.`;
}

export function gameBadgeLabel(game: AdbDetectedGame): string {
  if (game.id === "wos") return "WOS";
  return game.label || game.id.toUpperCase();
}

export function describeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function activityTag(tone: AdbActivityTone): string {
  if (tone === "success") return "ok";
  if (tone === "error") return "error";
  return "info";
}

export function formatActivityLine(entry: AdbActivityEntry): string {
  const detail = entry.detail ? ` — ${entry.detail}` : "";
  return `[${entry.at}] ${activityTag(entry.tone).padEnd(5)} ${entry.label}${detail}`;
}
