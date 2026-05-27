import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OnboardingWizard } from "./OnboardingWizard";
import * as api from "@/lib/api";
import * as onboarding from "@/lib/onboarding";

const okEnv: onboarding.EnvHealth = {
  redis: { ok: true, latency_ms: 1.2 },
  tesseract: { ok: true, path: "/usr/bin/tesseract", version: "tesseract 5.5.2" },
  adb: { ok: true, path: "/usr/bin/adb", version: "Android Debug Bridge 1.0.41" },
};

const noDeviceState: onboarding.OnboardingState = {
  device_added_at: null,
  bot_started_at: null,
  first_scenario_at: null,
  first_approval_at: null,
  first_ocr_at: null,
};

const deviceState: onboarding.OnboardingState = {
  ...noDeviceState,
  device_added_at: "2026-01-01T00:00:00Z",
};

function arrange({
  env = okEnv,
  state = noDeviceState,
  bot = { running: false },
}: {
  env?: onboarding.EnvHealth;
  state?: onboarding.OnboardingState;
  bot?: { running: boolean; pid?: number };
} = {}) {
  vi.spyOn(onboarding, "fetchEnvHealth").mockResolvedValue(env);
  vi.spyOn(onboarding, "fetchOnboardingState").mockResolvedValue(state);
  vi.spyOn(api, "fetchBotStatus").mockResolvedValue(
    bot as Awaited<ReturnType<typeof api.fetchBotStatus>>,
  );
  vi.spyOn(api, "startLocalBot").mockResolvedValue(
    { running: true, pid: 1234 } as Awaited<
      ReturnType<typeof api.startLocalBot>
    >,
  );
}

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OnboardingWizard", () => {
  it("does not render the dialog when wizard has already been seen", () => {
    window.localStorage.setItem("wos:onboarding:wizardSeen", "1");
    arrange();
    render(<OnboardingWizard />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens on mount when wizardSeen is unset", async () => {
    arrange();
    render(<OnboardingWizard />);
    expect(
      await screen.findByText("Welcome to Autopilot"),
    ).toBeInTheDocument();
  });

  it("starts on Environment step when Redis is down", async () => {
    arrange({
      env: {
        ...okEnv,
        redis: { ok: false, error: "connection refused" },
      },
    });
    render(<OnboardingWizard />);
    await waitFor(() => {
      expect(screen.getByText(/connection refused/)).toBeInTheDocument();
    });
    expect(screen.getByText("Recheck")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Next" }),
    ).toBeDisabled();
  });

  it("auto-jumps to Add-device when env is ok but no device is configured", async () => {
    arrange({ state: noDeviceState });
    render(<OnboardingWizard />);
    expect(
      await screen.findByText("Open ADB settings"),
    ).toBeInTheDocument();
  });

  it("auto-jumps to Start-bot when env ok + device added", async () => {
    arrange({ state: deviceState });
    render(<OnboardingWizard />);
    expect(
      await screen.findByRole("button", { name: "Start bot" }),
    ).toBeInTheDocument();
  });

  it("shows running state on Step 3 when the bot is already up", async () => {
    arrange({ state: deviceState, bot: { running: true, pid: 1 } });
    render(<OnboardingWizard />);
    expect(
      await screen.findByText("✓ The bot is running."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Finish" }),
    ).toBeInTheDocument();
  });

  it("calls startLocalBot when the Start-bot button is clicked", async () => {
    arrange({ state: deviceState, bot: { running: false } });
    render(<OnboardingWizard />);
    const startBtn = await screen.findByRole("button", { name: "Start bot" });
    await userEvent.click(startBtn);
    expect(api.startLocalBot).toHaveBeenCalledOnce();
  });

  it("marks the wizard as seen and closes when Skip is clicked", async () => {
    arrange();
    render(<OnboardingWizard />);
    const skip = await screen.findByText("Skip");
    await userEvent.click(skip);
    expect(window.localStorage.getItem("wos:onboarding:wizardSeen")).toBe("1");
    await waitFor(() => {
      expect(
        screen.queryByText("Welcome to Autopilot"),
      ).not.toBeInTheDocument();
    });
  });

  it("marks the wizard as seen and closes when Finish is clicked", async () => {
    arrange({ state: deviceState, bot: { running: true, pid: 1 } });
    render(<OnboardingWizard />);
    const finish = await screen.findByRole("button", { name: "Finish" });
    await userEvent.click(finish);
    expect(window.localStorage.getItem("wos:onboarding:wizardSeen")).toBe("1");
  });
});
