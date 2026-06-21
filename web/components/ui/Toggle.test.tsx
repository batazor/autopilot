import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Toggle } from "./Toggle";

describe("Toggle", () => {
  it("renders a switch reflecting the checked state", () => {
    render(<Toggle checked onChange={vi.fn()} aria-label="Wifi" />);
    const sw = screen.getByRole("switch", { name: "Wifi" });
    expect(sw).toHaveAttribute("aria-checked", "true");
  });

  it("emits the negated value on click", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} aria-label="Wifi" />);

    await user.click(screen.getByRole("switch", { name: "Wifi" }));

    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("is inert when disabled", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle checked onChange={onChange} disabled aria-label="Wifi" />);

    const sw = screen.getByRole("switch", { name: "Wifi" });
    expect(sw).toBeDisabled();
    await user.click(sw);

    expect(onChange).not.toHaveBeenCalled();
  });

  it("applies an id so a label can target it", () => {
    render(<Toggle id="opt-x" checked={false} onChange={vi.fn()} aria-label="Wifi" />);
    expect(screen.getByRole("switch", { name: "Wifi" })).toHaveAttribute("id", "opt-x");
  });
});
