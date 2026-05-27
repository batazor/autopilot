import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueueCreateTaskDialog } from "./QueueCreateTaskDialog";
import * as api from "@/lib/api";
import type { ScenarioRow } from "@/lib/config-pages";

const deviceLevelScenario: ScenarioRow = {
  key: "core/popup/dismiss",
  name: "Dismiss popup",
  enabled: true,
  device_level: true,
  steps: 3,
  source: "core/popup",
  path: "games/wos/core/popup/scenarios/dismiss.yaml",
};

const accountScenario: ScenarioRow = {
  key: "heroes/promote",
  name: "Promote hero",
  enabled: true,
  device_level: false,
  steps: 5,
  source: "heroes",
  path: "games/wos/heroes/scenarios/promote.yaml",
};

function arrange(overrides: Partial<Parameters<typeof QueueCreateTaskDialog>[0]> = {}) {
  vi.spyOn(api, "fetchModuleScenarios").mockResolvedValue([
    deviceLevelScenario,
    accountScenario,
  ]);
  vi.spyOn(api, "fetchInstances").mockResolvedValue(["inst-1", "inst-2"]);
  const onClose = vi.fn();
  const onCreated = vi.fn();
  const onError = vi.fn();
  const utils = render(
    <QueueCreateTaskDialog
      open
      defaultScheduledAt={1_700_000_000}
      onClose={onClose}
      onCreated={onCreated}
      onError={onError}
      {...overrides}
    />,
  );
  return { ...utils, onClose, onCreated, onError };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("QueueCreateTaskDialog", () => {
  it("renders nothing when open=false", () => {
    vi.spyOn(api, "fetchModuleScenarios").mockResolvedValue([]);
    vi.spyOn(api, "fetchInstances").mockResolvedValue([]);
    render(
      <QueueCreateTaskDialog
        open={false}
        defaultScheduledAt={null}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
      />,
    );
    expect(screen.queryByText(/schedule task/i)).not.toBeInTheDocument();
  });

  it("shows the dialog and starts with Schedule disabled", async () => {
    arrange();
    expect(await screen.findByText(/schedule task/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^schedule$/i })).toBeDisabled();
  });

  it("loads scenarios and instances when opened", async () => {
    arrange();
    await waitFor(() => {
      expect(api.fetchModuleScenarios).toHaveBeenCalledWith("all");
      expect(api.fetchInstances).toHaveBeenCalled();
    });
  });

  it("pre-fills player id from default props", async () => {
    arrange({ defaultPlayerId: "p-42" });
    expect(await screen.findByDisplayValue("p-42")).toBeInTheDocument();
  });

  it("clears player id when none is provided", async () => {
    arrange();
    await screen.findByText(/schedule task/i);
    const playerInput = screen.getByPlaceholderText(/e\.g\. 123456/i) as
      | HTMLInputElement
      | undefined;
    expect(playerInput?.value ?? "").toBe("");
  });

  it("calls onClose when Cancel is clicked", async () => {
    const user = userEvent.setup();
    const { onClose } = arrange();
    await screen.findByText(/schedule task/i);
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("routes scenario fetch errors to onError", async () => {
    vi.spyOn(api, "fetchModuleScenarios").mockRejectedValue(
      new Error("redis down"),
    );
    vi.spyOn(api, "fetchInstances").mockResolvedValue([]);
    const onError = vi.fn();
    render(
      <QueueCreateTaskDialog
        open
        defaultScheduledAt={null}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={onError}
      />,
    );
    await waitFor(() => expect(onError).toHaveBeenCalledWith("redis down"));
  });
});
