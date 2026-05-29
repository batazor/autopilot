import { describe, expect, it } from "vitest";

import { evaluateAdbReadiness } from "@/lib/adb-device-ready";
import type { AdbDeviceRow, AdbStatus } from "@/lib/config-pages";

const configuredDevice: AdbDeviceRow = {
  name: "bs1",
  adb_serial: "127.0.0.1:5555",
  instance_id: "",
  bluestacks_window_title: "",
  screenshot_backend: "",
  screenshot_backend_effective: "quartz",
  input_backend: "",
  input_backend_effective: "scrcpy",
};

function status(overrides: Partial<AdbStatus>): AdbStatus {
  return {
    adb_executable: "adb",
    devices_yaml: "db/state/state.db",
    settings_yaml: "src/config/_settings_data.py",
    configured: [],
    live_devices: [],
    scan_error: null,
    ...overrides,
  };
}

describe("evaluateAdbReadiness", () => {
  it("matches emulator serial aliases to localhost ADB ports", () => {
    expect(
      evaluateAdbReadiness(
        status({
          configured: [configuredDevice],
          live_devices: [
            {
              serial: "emulator-5554",
              canonical_serial: "127.0.0.1:5555",
              line: "emulator-5554 device product:bluestacks",
            },
          ],
        }),
      ),
    ).toEqual({ ok: true });
  });
});
