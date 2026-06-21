import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_PORT_RANGE,
  loadScanPortRange,
  saveScanPortRange,
} from "./adb-scan-prefs";

afterEach(() => {
  window.localStorage.clear();
});

describe("loadScanPortRange / saveScanPortRange", () => {
  it("returns the default when nothing is stored", () => {
    expect(loadScanPortRange()).toEqual(DEFAULT_PORT_RANGE);
  });

  it("round-trips a saved range", () => {
    const range = { start: "5550", end: "5560", step: "5" };
    saveScanPortRange(range);
    expect(loadScanPortRange()).toEqual(range);
  });

  it("falls back to the default on corrupt JSON", () => {
    window.localStorage.setItem("wos.adb.scanPortRange", "{not json");
    expect(loadScanPortRange()).toEqual(DEFAULT_PORT_RANGE);
  });

  it("falls back to the default on non-numeric fields", () => {
    window.localStorage.setItem(
      "wos.adb.scanPortRange",
      JSON.stringify({ start: "abc", end: "5560", step: "5" }),
    );
    expect(loadScanPortRange()).toEqual(DEFAULT_PORT_RANGE);
  });

  it("falls back to the default when a field is missing", () => {
    window.localStorage.setItem(
      "wos.adb.scanPortRange",
      JSON.stringify({ start: "5550", end: "5560" }),
    );
    expect(loadScanPortRange()).toEqual(DEFAULT_PORT_RANGE);
  });
});
