import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LabelingReferencePanel } from "./LabelingReferencePanel";
import type { LabelingReferenceMeta } from "@/lib/types";

function meta(overrides: Partial<LabelingReferenceMeta>): LabelingReferenceMeta {
  return {
    rel: "references/heroes.png",
    name: "heroes",
    rel_under: "heroes",
    title: "Heroes",
    screen_id: "heroes",
    region_count: 1,
    active_version: null,
    unassigned: false,
    ...overrides,
  };
}

const REFS: LabelingReferenceMeta[] = [
  meta({ rel: "references/heroes.png", name: "heroes", screen_id: "heroes" }),
  meta({ rel: "references/mail.png", name: "mail", screen_id: "mail" }),
  meta({
    rel: "references/building.png",
    name: "building",
    screen_id: "building",
  }),
];

function renderPanel(overrides: Partial<Parameters<typeof LabelingReferencePanel>[0]> = {}) {
  const onSelect = vi.fn();
  render(
    <LabelingReferencePanel
      scopes={[]}
      moduleScope="all"
      onModuleChange={vi.fn()}
      refs={REFS}
      refRel="references/heroes.png"
      filter=""
      onFilterChange={vi.fn()}
      onSelect={onSelect}
      basename=""
      onBasenameChange={vi.fn()}
      isPending={false}
      busy={false}
      onPromoteOrRename={vi.fn()}
      {...overrides}
    />,
  );
  return { onSelect };
}

afterEach(() => vi.restoreAllMocks());

describe("LabelingReferencePanel auto-select", () => {
  it("auto-selects the first match when the filter excludes the current selection", async () => {
    const { onSelect } = renderPanel({
      refRel: "references/heroes.png",
      filter: "mail",
    });
    await waitFor(() =>
      expect(onSelect).toHaveBeenCalledWith("references/mail.png"),
    );
  });

  it("leaves the selection alone when it still matches the filter", async () => {
    const { onSelect } = renderPanel({
      refRel: "references/mail.png",
      filter: "mail",
    });
    // Give the effect a chance to (not) fire.
    await new Promise((r) => setTimeout(r, 30));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("does not force a selection when the filter is empty", async () => {
    const { onSelect } = renderPanel({
      refRel: "references/heroes.png",
      filter: "",
    });
    await new Promise((r) => setTimeout(r, 30));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("does nothing when no reference matches the filter", async () => {
    const { onSelect } = renderPanel({
      refRel: "references/heroes.png",
      filter: "zzz-nomatch",
    });
    await new Promise((r) => setTimeout(r, 30));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
