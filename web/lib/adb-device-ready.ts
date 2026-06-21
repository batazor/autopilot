import type { AdbStatus } from "@/lib/config-pages";
import { adbSerialAliases } from "@/lib/adb-serial";

export type AdbReadinessProblem = "scan_error" | "no_devices" | "not_configured";

export type AdbReadiness =
  | { ok: true }
  | { ok: false; kind: AdbReadinessProblem; message: string };

/** Whether at least one configured (or any live) ADB device is ready before starting the bot. */
export function evaluateAdbReadiness(adb: AdbStatus): AdbReadiness {
  if (adb.scan_error?.trim()) {
    return { ok: false, kind: "scan_error", message: adb.scan_error.trim() };
  }
  const live = adb.live_devices ?? [];
  if (live.length === 0) {
    return {
      ok: false,
      kind: "no_devices",
      message: "No emulator or device is connected via ADB.",
    };
  }
  const liveSerials = new Set(
    live.flatMap((d) => adbSerialAliases(d.serial, d.canonical_serial)),
  );
  const configured = (adb.configured ?? []).filter((c) => c.adb_serial?.trim());
  if (configured.length === 0) {
    return { ok: true };
  }
  const anyMatch = configured.some((c) =>
    adbSerialAliases(c.adb_serial).some((alias) => liveSerials.has(alias)),
  );
  if (!anyMatch) {
    return {
      ok: false,
      kind: "not_configured",
      message:
        "devices.yaml has no online match — check ADB serials against live devices.",
    };
  }
  return { ok: true };
}

export function adbReadinessTitle(kind: AdbReadinessProblem): string {
  switch (kind) {
    case "scan_error":
      return "ADB scan failed";
    case "no_devices":
      return "No device connected";
    default:
      return "Device not ready";
  }
}
