import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NewModuleDialog } from "./NewModuleDialog";
import * as api from "@/lib/api";
import type { ModuleRow } from "@/lib/config-pages";

const sampleRow: ModuleRow = {
  id: "my_feature",
  storage_key: "my_feature",
  title: "My Feature",
  description: "desc",
  wiki: false,
  core: false,
  rel_path: "games/wos/my_feature",
  scenarios_dir: "games/wos/my_feature/scenarios",
  has_analyze: true,
  scenario_count: 0,
  enabled_on: 0,
  enabled_off: 0,
  scenarios: [],
};

afterEach(() => {
  vi.restoreAllMocks();
});

function arrange(overrides: Partial<Parameters<typeof NewModuleDialog>[0]> = {}) {
  const onClose = vi.fn();
  const onCreated = vi.fn();
  const onError = vi.fn();
  const utils = render(
    <NewModuleDialog
      open
      onClose={onClose}
      onCreated={onCreated}
      onError={onError}
      {...overrides}
    />,
  );
  return { ...utils, onClose, onCreated, onError };
}

describe("NewModuleDialog", () => {
  it("disables Create until id is valid and title is set", async () => {
    const user = userEvent.setup();
    arrange();
    const create = screen.getByRole("button", { name: /create/i });
    expect(create).toBeDisabled();

    await user.type(screen.getByPlaceholderText(/my_feature/i), "Bad-ID");
    await user.type(screen.getByPlaceholderText(/my feature/i), "Title");
    expect(create).toBeDisabled();
    expect(
      screen.getByText(/must start with a lowercase letter/i),
    ).toBeInTheDocument();

    await user.clear(screen.getByPlaceholderText(/my_feature/i));
    await user.type(screen.getByPlaceholderText(/my_feature/i), "my_feature");
    expect(create).toBeEnabled();
  });

  it("calls createModule with trimmed values and notifies onCreated", async () => {
    const user = userEvent.setup();
    const spy = vi
      .spyOn(api, "createModule")
      .mockResolvedValue(sampleRow);
    const { onCreated, onClose, onError } = arrange();

    await user.type(screen.getByPlaceholderText(/my_feature/i), "  my_feature  ");
    await user.type(screen.getByPlaceholderText(/my feature/i), "  My Feature  ");
    await user.type(
      screen.getByPlaceholderText(/what this module automates/i),
      "  desc  ",
    );
    await user.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(
      {
        id: "my_feature",
        title: "My Feature",
        description: "desc",
        parent: "",
        wiki: false,
      },
      "wos",
    );
    expect(onCreated).toHaveBeenCalledWith(sampleRow);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it("saves the module under the selected game (initialGame)", async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(api, "createModule").mockResolvedValue(sampleRow);
    arrange({ initialGame: "kingshot" });

    await user.type(screen.getByPlaceholderText(/my_feature/i), "ks_feature");
    await user.type(screen.getByPlaceholderText(/my feature/i), "KS Feature");
    await user.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ id: "ks_feature" }),
      "kingshot",
    );
  });

  it("routes API errors to onError and keeps the dialog open", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "createModule").mockRejectedValue(
      new Error("409 — module id already taken"),
    );
    const { onCreated, onClose, onError } = arrange();

    await user.type(screen.getByPlaceholderText(/my_feature/i), "my_feature");
    await user.type(screen.getByPlaceholderText(/my feature/i), "My Feature");
    await user.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() =>
      expect(onError).toHaveBeenCalledWith("409 — module id already taken"),
    );
    expect(onCreated).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
