import { describe, expect, it } from "vitest";

import {
  createModuleSearchIndex,
  filterModuleSearchIndex,
  filterModules,
} from "./modules-filter";
import type { ModuleRow } from "@/lib/config-pages";

function mod(overrides: Partial<ModuleRow>): ModuleRow {
  return {
    id: "heroes",
    storage_key: "wos/heroes",
    title: "Heroes",
    description: "Promote and recruit heroes",
    wiki: false,
    core: true,
    rel_path: "games/wos/core/heroes",
    scenarios_dir: "games/wos/core/heroes/scenarios",
    has_analyze: true,
    scenario_count: 3,
    enabled_on: 2,
    enabled_off: 1,
    scenarios: [],
    ...overrides,
  };
}

const ROWS: ModuleRow[] = [
  mod({ id: "heroes", title: "Heroes", storage_key: "wos/heroes" }),
  mod({
    id: "mail",
    title: "Mail",
    storage_key: "wos/mail",
    description: "Claim mail and gifts",
    rel_path: "games/wos/mail",
  }),
  mod({
    id: "gift_codes",
    title: "Gift Codes",
    storage_key: "wos/gift_codes",
    description: "Redeem promo codes",
    rel_path: "games/wos/gift_codes",
  }),
];

describe("filterModules", () => {
  it("returns the full list for an empty query", () => {
    expect(filterModules(ROWS, "")).toHaveLength(3);
  });

  it("returns the full list for a whitespace-only query", () => {
    expect(filterModules(ROWS, "   ")).toHaveLength(3);
  });

  it("matches on id", () => {
    const r = filterModules(ROWS, "mail");
    expect(r.map((m) => m.id)).toEqual(["mail"]);
  });

  it("matches on title case-insensitively", () => {
    expect(filterModules(ROWS, "HEROES").map((m) => m.id)).toEqual(["heroes"]);
  });

  it("matches on description", () => {
    expect(filterModules(ROWS, "redeem").map((m) => m.id)).toEqual(["gift_codes"]);
  });

  it("matches on storage_key", () => {
    expect(filterModules(ROWS, "wos/gift").map((m) => m.id)).toEqual(["gift_codes"]);
  });

  it("matches on rel_path", () => {
    expect(filterModules(ROWS, "games/wos/mail").map((m) => m.id)).toEqual(["mail"]);
  });

  it("trims surrounding whitespace before matching", () => {
    expect(filterModules(ROWS, "  heroes  ").map((m) => m.id)).toEqual(["heroes"]);
  });

  it("returns an empty list when nothing matches", () => {
    expect(filterModules(ROWS, "nonexistent-zzz")).toEqual([]);
  });

  it("can match multiple rows on a shared token", () => {
    // every fixture row has storage_key starting with "wos/"
    expect(filterModules(ROWS, "wos/")).toHaveLength(3);
  });

  it("filters a precomputed search index with the same matching rules", () => {
    const index = createModuleSearchIndex(ROWS);

    expect(filterModuleSearchIndex(index, "HEROES").map((m) => m.id)).toEqual([
      "heroes",
    ]);
    expect(filterModuleSearchIndex(index, "redeem").map((m) => m.id)).toEqual([
      "gift_codes",
    ]);
  });

  it("keeps empty indexed searches in the original order", () => {
    const index = createModuleSearchIndex(ROWS);

    expect(filterModuleSearchIndex(index, "").map((m) => m.id)).toEqual([
      "heroes",
      "mail",
      "gift_codes",
    ]);
  });
});
