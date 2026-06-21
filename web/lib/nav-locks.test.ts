import { describe, expect, it } from "vitest";
import { getNavLock } from "./nav-locks";

describe("nav locks", () => {
  it("flags work-in-progress routes as a non-disabling 'wip' lock", () => {
    expect(getNavLock("/notify-monitor")?.kind).toBe("wip");
  });

  it("returns null for unlocked routes", () => {
    expect(getNavLock("/overview")).toBeNull();
    expect(getNavLock("/alliance-stats")).toBeNull();
  });
});
