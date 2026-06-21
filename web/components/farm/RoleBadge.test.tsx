import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RoleBadge } from "./RoleBadge";
import type { RoleOption } from "@/lib/farm/types";

const ROLES: RoleOption[] = [
  { id: "balanced", label: "Balanced", description: "Even split." },
  { id: "farm", label: "Farm", description: "Resource gathering first." },
  { id: "fighter", label: "Fighter", description: "Combat first." },
];

describe("RoleBadge", () => {
  it("shows the current role label", () => {
    render(<RoleBadge role="farm" roles={ROLES} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Farm/ })).toBeInTheDocument();
  });

  it("opens a menu and switches the role on pick", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<RoleBadge role="balanced" roles={ROLES} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: /Balanced/ }));
    await user.click(screen.getByRole("menuitemradio", { name: /Fighter/ }));

    expect(onChange).toHaveBeenCalledWith("fighter");
    // menu closes after a pick
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("does not fire onChange when re-picking the current role", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<RoleBadge role="farm" roles={ROLES} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: /Farm/ }));
    await user.click(screen.getByRole("menuitemradio", { name: /Farm/ }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("is inert when disabled", async () => {
    const user = userEvent.setup();
    render(<RoleBadge role="farm" roles={ROLES} disabled onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Farm/ }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
