import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-confetti", () => ({
  default: (props: { className?: string }) => (
    <canvas data-testid="onboarding-confetti" className={props.className} />
  ),
}));

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
  approvals_disabled_at: null,
};

const deviceState: onboarding.OnboardingState = {
  ...noDeviceState,
  device_added_at: "2026-01-01T00:00:00Z",
};

const activeLicense: Awaited<ReturnType<typeof api.fetchLicenseStatus>> = {
  active: true,
  state: "active",
  reason: null,
  sub: "operator",
  tier: "pro",
  features: ["core"],
  expires_at: null,
  days_left: null,
  machine_id: "machine-1",
  max_devices: 2,
  max_players_per_device: 3,
  admin_enabled: false,
  license_file: ".secrets/license.jwt",
};

const adbStatus: Awaited<ReturnType<typeof api.fetchAdbStatus>> = {
  adb_executable: "adb",
  devices_yaml: "db/state/state.db",
  settings_yaml: "src/config/_settings_data.py",
  configured: [],
  live_devices: [],
  scan_error: null,
};

function arrange({
  env = okEnv,
  state = noDeviceState,
  bot = { running: false },
  adb = adbStatus,
  license = activeLicense,
}: {
  env?: onboarding.EnvHealth;
  state?: onboarding.OnboardingState;
  bot?: { running: boolean; pid?: number };
  adb?: Awaited<ReturnType<typeof api.fetchAdbStatus>>;
  license?: Awaited<ReturnType<typeof api.fetchLicenseStatus>>;
} = {}) {
  vi.spyOn(api, "fetchLicenseStatus").mockResolvedValue(license);
  vi.spyOn(onboarding, "fetchEnvHealth").mockResolvedValue(env);
  vi.spyOn(onboarding, "fetchOnboardingState").mockResolvedValue(state);
  const fetchAdbSpy = vi.spyOn(api, "fetchAdbStatus").mockResolvedValue(adb);
  vi.spyOn(api, "fetchBotStatus").mockResolvedValue(
    bot as Awaited<ReturnType<typeof api.fetchBotStatus>>,
  );
  vi.spyOn(api, "startLocalBot").mockResolvedValue(
    { running: true, pid: 1234 } as Awaited<
      ReturnType<typeof api.startLocalBot>
    >,
  );
  vi.spyOn(api, "importLicenseFile").mockResolvedValue({
    ok: true,
    license_file: ".secrets/license.jwt",
    status: activeLicense,
  });
  return { fetchAdbSpy };
}

beforeEach(() => {
  vi.useRealTimers();
  window.localStorage.clear();
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

  it("starts on License step when the license is missing", async () => {
    arrange({
      license: {
        ...activeLicense,
        active: false,
        state: "missing",
        tier: null,
        reason: "license file not found",
      },
    });
    render(<OnboardingWizard />);
    expect(await screen.findByText("Open license")).toBeInTheDocument();
    expect(screen.getByText("license file not found")).toBeInTheDocument();
    expect(
      screen.getByRole("link", {
        name: "Get a trial license file on Discord",
      }),
    ).toHaveAttribute("href", "https://discord.gg/62twnzKG9");
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("imports a license file from the License step", async () => {
    arrange({
      license: {
        ...activeLicense,
        active: false,
        state: "missing",
        tier: null,
        reason: "license file not found",
      },
    });
    render(<OnboardingWizard />);
    const input = await screen.findByLabelText("Import license file");
    const file = new File(["token"], "license.jwt", {
      type: "application/jwt",
    });

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(api.importLicenseFile).toHaveBeenCalledWith(file);
    });
    expect(await screen.findByText("✓ License active · pro")).toBeInTheDocument();
    expect(screen.queryByLabelText("Import license file")).not.toBeInTheDocument();
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

  it("shows current configured and live devices in the Add-device step", async () => {
    arrange({
      state: noDeviceState,
      adb: {
        ...adbStatus,
        configured: [
          {
            name: "bs1",
            adb_serial: "emulator-5554",
            instance_id: "",
            bluestacks_window_title: "",
            screenshot_backend: "",
            screenshot_backend_effective: "scrcpy",
            input_backend: "",
            input_backend_effective: "scrcpy",
          },
        ],
        live_devices: [
          {
            serial: "emulator-5554",
            line: "emulator-5554 device product:bluestacks",
          },
        ],
      },
    });
    render(<OnboardingWizard />);
    expect(await screen.findByText("Current devices")).toBeInTheDocument();
    expect(screen.getByText("Configured 1")).toBeInTheDocument();
    expect(screen.getByText("Live 1")).toBeInTheDocument();
    expect(screen.getByText("bs1")).toBeInTheDocument();
    expect(screen.getByText("emulator-5554")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).not.toBeDisabled();
  });

  it("matches configured 127.0.0.1:5555 to live emulator-5554", async () => {
    arrange({
      state: noDeviceState,
      adb: {
        ...adbStatus,
        configured: [
          {
            name: "bs1",
            adb_serial: "127.0.0.1:5555",
            instance_id: "",
            bluestacks_window_title: "",
            screenshot_backend: "",
            screenshot_backend_effective: "scrcpy",
            input_backend: "",
            input_backend_effective: "scrcpy",
          },
        ],
        live_devices: [
          {
            serial: "emulator-5554",
            canonical_serial: "127.0.0.1:5555",
            line: "emulator-5554 device product:bluestacks",
          },
        ],
      },
    });
    render(<OnboardingWizard />);
    expect(await screen.findByText("127.0.0.1:5555")).toBeInTheDocument();
    expect(screen.getByText("live")).toBeInTheDocument();
    expect(screen.queryByText("Detected by ADB")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).not.toBeDisabled();
  });

  it("enables Next when ADB finds a live device that is not configured yet", async () => {
    arrange({
      state: noDeviceState,
      adb: {
        ...adbStatus,
        live_devices: [
          {
            serial: "RF8RC00M8MF",
            line: "RF8RC00M8MF device product:phone",
          },
        ],
      },
    });
    render(<OnboardingWizard />);
    expect(
      await screen.findByText("✓ At least one device is detected."),
    ).toBeInTheDocument();
    expect(screen.getByText("Detected by ADB")).toBeInTheDocument();
    expect(screen.getByText("RF8RC00M8MF")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).not.toBeDisabled();
  });

  it("Refresh rescans ADB and unlocks Next when a device appears", async () => {
    const { fetchAdbSpy } = arrange({ state: noDeviceState });
    render(<OnboardingWizard />);
    expect(await screen.findByText("No current devices found.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

    fetchAdbSpy.mockResolvedValue({
      ...adbStatus,
      live_devices: [
        {
          serial: "RF8RC00M8MF",
          line: "RF8RC00M8MF device product:phone",
        },
      ],
    });
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText("RF8RC00M8MF")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).not.toBeDisabled();
  });

  it("auto-jumps to Start-bot when env ok + device added", async () => {
    arrange({ state: deviceState });
    render(<OnboardingWizard />);
    expect(
      await screen.findByRole("button", { name: "Start bot" }),
    ).toBeInTheDocument();
  });

  it("stays closed and marks the wizard seen when setup is already complete", async () => {
    arrange({ state: deviceState, bot: { running: true, pid: 1 } });
    render(<OnboardingWizard />);
    await waitFor(() => {
      expect(window.localStorage.getItem("wos:onboarding:wizardSeen")).toBe("1");
    });
    expect(screen.queryByText("Welcome to Autopilot")).not.toBeInTheDocument();
    expect(screen.queryByTestId("onboarding-confetti")).not.toBeInTheDocument();
  });

  it("calls startLocalBot when the Start-bot button is clicked", async () => {
    arrange({ state: deviceState, bot: { running: false } });
    render(<OnboardingWizard />);
    const startBtn = await screen.findByRole("button", { name: "Start bot" });
    await userEvent.click(startBtn);
    expect(api.startLocalBot).toHaveBeenCalledOnce();
  });

  it("fires confetti once when the bot transitions to running, with a persistent flag", async () => {
    arrange({ state: deviceState, bot: { running: false } });
    render(<OnboardingWizard />);
    const startBtn = await screen.findByRole("button", { name: "Start bot" });
    vi.spyOn(api, "fetchBotStatus").mockResolvedValue(
      { running: true, pid: 1234 } as Awaited<
        ReturnType<typeof api.fetchBotStatus>
      >,
    );
    await userEvent.click(startBtn);
    expect(
      await screen.findByText("✓ The bot is running."),
    ).toBeInTheDocument();
    expect(screen.getByTestId("onboarding-confetti")).toBeInTheDocument();
    expect(
      window.localStorage.getItem("wos:onboarding:wizardCelebrated"),
    ).toBe("1");
  });

  it("does not re-fire confetti when it has already been celebrated", async () => {
    window.localStorage.setItem("wos:onboarding:wizardCelebrated", "1");
    arrange({ state: deviceState, bot: { running: false } });
    render(<OnboardingWizard />);
    const startBtn = await screen.findByRole("button", { name: "Start bot" });
    vi.spyOn(api, "fetchBotStatus").mockResolvedValue(
      { running: true, pid: 1234 } as Awaited<
        ReturnType<typeof api.fetchBotStatus>
      >,
    );
    await userEvent.click(startBtn);
    expect(
      await screen.findByText("✓ The bot is running."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("onboarding-confetti")).not.toBeInTheDocument();
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
    arrange({ state: deviceState, bot: { running: false } });
    render(<OnboardingWizard />);
    const finish = await screen.findByRole("button", { name: "Finish" });
    await userEvent.click(finish);
    expect(window.localStorage.getItem("wos:onboarding:wizardSeen")).toBe("1");
  });
});
