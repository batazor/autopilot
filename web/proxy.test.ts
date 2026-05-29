import { describe, expect, it } from "vitest";

import { isPublic } from "./proxy";

describe("proxy public routes", () => {
  it("lets UI image assets load before license state is known", () => {
    expect(isPublic("/logo.png")).toBe(true);
    expect(isPublic("/games/wos.webp")).toBe(true);
    expect(isPublic("/favicon.ico")).toBe(true);
  });

  it("still protects app pages", () => {
    expect(isPublic("/overview")).toBe(false);
    expect(isPublic("/player-state")).toBe(false);
  });
});
