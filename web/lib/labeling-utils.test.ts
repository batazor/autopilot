import { describe, expect, it } from "vitest";

import { inferScopeFromRef } from "./labeling-utils";

describe("inferScopeFromRef", () => {
  it("infers game-prefixed scopes from module reference paths", () => {
    expect(
      inferScopeFromRef("games/wos/core/who_i_am/references/chief_profile.png"),
    ).toBe("wos:core/who_i_am");
    expect(
      inferScopeFromRef("games/wos/deals/deals/references/main_city.png"),
    ).toBe("wos:deals/deals");
  });

  it("keeps legacy modules/ support", () => {
    expect(inferScopeFromRef("modules/vip/references/page.vip.png")).toBe("vip");
  });

  it("does not infer a scope for root references", () => {
    expect(inferScopeFromRef("references/main_city.png")).toBeNull();
  });
});
