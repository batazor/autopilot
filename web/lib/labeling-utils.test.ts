import { describe, expect, it } from "vitest";

import {
  docMatchesRef,
  inferScopeFromRef,
  nextRefAfterRemoval,
  resolveImageRef,
  resolveSelectedRef,
} from "./labeling-utils";
import type { LabelingDocument, LabelingReferenceMeta } from "./types";

function ref(rel: string): LabelingReferenceMeta {
  return {
    rel,
    name: rel.split("/").pop() ?? rel,
    rel_under: rel,
    title: rel,
    screen_id: "",
    region_count: 0,
    active_version: null,
  };
}

const PUBLISHED = "games/wos/events/fishing_tournament/references/main_city.to.fishing_tournament.png";
const OTHER = "games/wos/events/fishing_tournament/references/other.png";
const TEMPORAL = "games/wos/events/fishing_tournament/references/temporal/bs1_shot_20260531_154143_b7eb8d.png";

describe("inferScopeFromRef", () => {
  it("infers game-prefixed scopes from module reference paths", () => {
    expect(
      inferScopeFromRef("games/wos/core/chief_profile/references/chief_profile.png"),
    ).toBe("wos:core/chief_profile");
    expect(
      inferScopeFromRef("games/wos/deals/deals/references/main_city.png"),
    ).toBe("wos:deals/deals");
  });

  it("infers the WOS beta catalog scope from nested beta references", () => {
    expect(
      inferScopeFromRef("games/wos/beta/account/switch/references/account_switch.png"),
    ).toBe("wos_beta:account/switch");
  });

  it("keeps legacy modules/ support", () => {
    expect(inferScopeFromRef("modules/vip/references/page.vip.png")).toBe("vip");
  });

  it("does not infer a scope for root references", () => {
    expect(inferScopeFromRef("references/main_city.png")).toBeNull();
  });
});

describe("resolveSelectedRef", () => {
  it("honors a URL ref that still exists in the list", () => {
    const list = [ref(PUBLISHED), ref(OTHER)];
    expect(
      resolveSelectedRef({ list, urlRef: OTHER, currentRef: "" }),
    ).toBe(OTHER);
  });

  it("drops a stale URL ref (rotated/deleted temporal capture) and falls back", () => {
    // The exact 404 case from the bug report: URL points at a temporal capture
    // that no longer exists on disk.
    const list = [ref(PUBLISHED)];
    expect(
      resolveSelectedRef({ list, urlRef: TEMPORAL, currentRef: "" }),
    ).toBe(PUBLISHED);
  });

  it("keeps a still-valid current selection when no URL ref is given", () => {
    const list = [ref(PUBLISHED), ref(OTHER)];
    expect(
      resolveSelectedRef({ list, urlRef: null, currentRef: OTHER }),
    ).toBe(OTHER);
  });

  it("replaces a current selection that went stale (deleted out from under us)", () => {
    const list = [ref(OTHER)];
    expect(
      resolveSelectedRef({ list, urlRef: null, currentRef: PUBLISHED }),
    ).toBe(OTHER);
  });

  it("returns null to clear the selection when the list is empty", () => {
    expect(
      resolveSelectedRef({ list: [], urlRef: TEMPORAL, currentRef: PUBLISHED }),
    ).toBeNull();
  });

  it("prefers the URL ref over a different valid current selection", () => {
    const list = [ref(PUBLISHED), ref(OTHER)];
    expect(
      resolveSelectedRef({ list, urlRef: PUBLISHED, currentRef: OTHER }),
    ).toBe(PUBLISHED);
  });

  it("keeps an explicit current selection while the URL is catching up", () => {
    const list = [ref(PUBLISHED), ref(TEMPORAL)];
    expect(
      resolveSelectedRef({
        list,
        urlRef: PUBLISHED,
        currentRef: TEMPORAL,
        preferCurrent: true,
      }),
    ).toBe(TEMPORAL);
  });
});

describe("nextRefAfterRemoval", () => {
  it("prefers a published reference over a pending capture", () => {
    const list = [ref(TEMPORAL), ref(PUBLISHED)];
    expect(nextRefAfterRemoval(list)).toBe(PUBLISHED);
  });

  it("falls back to the first entry when only captures remain", () => {
    const list = [ref(TEMPORAL)];
    expect(nextRefAfterRemoval(list)).toBe(TEMPORAL);
  });

  it("returns an empty string when nothing remains", () => {
    expect(nextRefAfterRemoval([])).toBe("");
  });
});

describe("docMatchesRef", () => {
  it("is false for a null doc", () => {
    expect(docMatchesRef(null, PUBLISHED)).toBe(false);
  });

  it("is false when the doc belongs to a different reference (stale/lagging load)", () => {
    expect(docMatchesRef({ ref: OTHER }, PUBLISHED)).toBe(false);
  });

  it("is true when the doc matches the current selection", () => {
    expect(docMatchesRef({ ref: PUBLISHED }, PUBLISHED)).toBe(true);
  });
});

describe("resolveImageRef", () => {
  it("returns the temporal ref directly without consulting the doc", () => {
    expect(resolveImageRef(TEMPORAL, null)).toBe(TEMPORAL);
  });

  it("falls back to refRel when the doc does not match (avoids cross-ref image)", () => {
    const staleDoc = {
      ref: OTHER,
      display_ref: OTHER,
    } as Pick<LabelingDocument, "ref" | "display_ref">;
    expect(resolveImageRef(PUBLISHED, staleDoc)).toBe(PUBLISHED);
  });

  it("uses the matching doc's display_ref (e.g. version-bound OCR image)", () => {
    const boundImage = "games/wos/events/fishing_tournament/references/main_city_v2.png";
    const doc = {
      ref: PUBLISHED,
      display_ref: boundImage,
    } as Pick<LabelingDocument, "ref" | "display_ref">;
    expect(resolveImageRef(PUBLISHED, doc)).toBe(boundImage);
  });

  it("falls back to refRel when a matching doc has an empty display_ref", () => {
    const doc = {
      ref: PUBLISHED,
      display_ref: "  ",
    } as Pick<LabelingDocument, "ref" | "display_ref">;
    expect(resolveImageRef(PUBLISHED, doc)).toBe(PUBLISHED);
  });
});
