import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LabelingImportConflictDialog } from "./LabelingImportConflictDialog";
import type { LabelingImportConflict } from "@/lib/types";

const existingBtn = { name: "btn", action: "exist", bbox: { x: 10, y: 10 } };
const incomingBtn = { name: "btn", action: "exist", bbox: { x: 90, y: 10 } }; // changed
const incomingExtra = { name: "extra", action: "text", bbox: { x: 5, y: 5 } }; // added
const existingGone = { name: "gone", action: "exist", bbox: { x: 1, y: 1 } }; // removed

const conflict: LabelingImportConflict = {
  existing_ref: "games/wos/ads/references/page.png",
  existing_screen_id: "ads_page",
  existing_regions: [existingBtn, existingGone],
  existing_has_image: true,
  matched_by: "screen_id",
  diff: { added: ["extra"], removed: ["gone"], changed: ["btn"], unchanged: [] },
};

function arrange() {
  const onApply = vi.fn();
  const onCancel = vi.fn();
  render(
    <LabelingImportConflictDialog
      open
      conflict={conflict}
      incomingRegions={[incomingBtn, incomingExtra]}
      incomingImageUrl="/incoming.png"
      existingImageUrl="/existing.png"
      busy={false}
      onCancel={onCancel}
      onApply={onApply}
    />,
  );
  return { onApply, onCancel };
}

describe("LabelingImportConflictDialog", () => {
  it("applies defaults: incoming for changed, add new, keep removed, existing image", async () => {
    const { onApply } = arrange();
    await userEvent.click(screen.getByRole("button", { name: /apply & save/i }));

    expect(onApply).toHaveBeenCalledTimes(1);
    const [regions, useIncomingImage] = onApply.mock.calls[0];
    const byName = Object.fromEntries(
      (regions as Record<string, unknown>[]).map((r) => [r.name, r]),
    );
    // changed → incoming variant (x: 90); added included; removed kept.
    expect((byName.btn as { bbox: { x: number } }).bbox.x).toBe(90);
    expect(byName.extra).toBeTruthy();
    expect(byName.gone).toBeTruthy();
    expect(useIncomingImage).toBe(false);
  });

  it("honors overrides: keep existing region, drop added, drop removed, use incoming image", async () => {
    const { onApply } = arrange();

    // changed btn → existing
    await userEvent.click(screen.getByRole("radio", { name: "existing" }));
    // added extra → uncheck
    await userEvent.click(screen.getByRole("checkbox", { name: /extra add/i }));
    // removed gone → uncheck "keep"
    await userEvent.click(screen.getByRole("checkbox", { name: /gone keep/i }));
    // image → use bundle screenshot
    await userEvent.click(screen.getByRole("radio", { name: /use bundle screenshot/i }));

    await userEvent.click(screen.getByRole("button", { name: /apply & save/i }));
    const [regions, useIncomingImage] = onApply.mock.calls[0];
    const byName = Object.fromEntries(
      (regions as Record<string, unknown>[]).map((r) => [r.name, r]),
    );
    expect((byName.btn as { bbox: { x: number } }).bbox.x).toBe(10); // existing kept
    expect(byName.extra).toBeUndefined();
    expect(byName.gone).toBeUndefined();
    expect(useIncomingImage).toBe(true);
  });
});
