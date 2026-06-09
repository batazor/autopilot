const PORT_RANGE_KEY = "wos.adb.scanPortRange";

export type AdbPortRangeForm = { start: string; end: string; step: string };

// Mirrors the backend default in src/api/services/adb_api.py.
export const DEFAULT_PORT_RANGE: AdbPortRangeForm = {
  start: "5555",
  end: "5625",
  step: "10",
};

function isValidField(v: unknown): v is string {
  return typeof v === "string" && /^\d+$/.test(v.trim());
}

export function loadScanPortRange(): AdbPortRangeForm {
  if (typeof window === "undefined") return DEFAULT_PORT_RANGE;
  try {
    const raw = window.localStorage.getItem(PORT_RANGE_KEY);
    if (!raw) return DEFAULT_PORT_RANGE;
    const parsed = JSON.parse(raw) as Partial<AdbPortRangeForm>;
    if (
      isValidField(parsed.start) &&
      isValidField(parsed.end) &&
      isValidField(parsed.step)
    ) {
      return { start: parsed.start, end: parsed.end, step: parsed.step };
    }
  } catch {
    /* corrupt JSON / quota / private mode — fall back to default */
  }
  return DEFAULT_PORT_RANGE;
}

export function saveScanPortRange(range: AdbPortRangeForm): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PORT_RANGE_KEY, JSON.stringify(range));
  } catch {
    /* quota / private mode — non-fatal, range just won't persist */
  }
}
