import { describe, expect, it } from "vitest";
import { getNavLock, tierAtLeast } from "./nav-locks";

describe("nav locks", () => {
  it("treats R5 as above R4 for alliance stats", () => {
    expect(tierAtLeast("r3", "r4")).toBe(false);
    expect(tierAtLeast("r4", "r4")).toBe(true);
    expect(tierAtLeast("r5", "r4")).toBe(true);

    expect(getNavLock("/alliance-stats", null)?.kind).toBe("r4");
    expect(getNavLock("/alliance-stats", "r3")?.kind).toBe("r4");
    expect(getNavLock("/alliance-stats", "r4")).toBeNull();
    expect(getNavLock("/alliance-stats", "r5")).toBeNull();
  });
});
