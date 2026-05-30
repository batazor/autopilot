"use client";

import {
  Dialog,
  DialogBackdrop,
  DialogPanel,
  DialogTitle,
} from "@headlessui/react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  fetchAdbStatus,
  fetchBotStatus,
  fetchLicenseStatus,
  importLicenseFile,
  startLocalBot,
} from "@/lib/api";
import { adbSerialAliases, adbSerialMatches } from "@/lib/adb-serial";
import { OnboardingConfetti } from "@/components/onboarding/OnboardingConfetti";
import type { AdbStatus, LicenseStatus } from "@/lib/config-pages";
import {
  type EnvHealth,
  type EnvHealthEntry,
  fetchEnvHealth,
  fetchOnboardingState,
  markWizardSeen,
  type OnboardingState,
  wizardSeen,
} from "@/lib/onboarding";

const STEP_LABELS = ["License", "Environment", "Add device", "Start bot"] as const;
const STEP_LICENSE = 0;
const STEP_ENVIRONMENT = 1;
const STEP_DEVICE = 2;
const STEP_BOT = 3;
const LAST_STEP = STEP_LABELS.length - 1;
const DISCORD_INVITE_URL = "https://discord.gg/62twnzKG9";

async function readLicenseStatus(): Promise<{
  status: LicenseStatus | null;
  error: string | null;
}> {
  try {
    return { status: await fetchLicenseStatus(), error: null };
  } catch (err) {
    return {
      status: null,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

async function readAdbStatus(): Promise<{
  status: AdbStatus | null;
  error: string | null;
}> {
  try {
    return { status: await fetchAdbStatus(), error: null };
  } catch (err) {
    return {
      status: null,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

function hasCurrentDevice(status: AdbStatus | null): boolean {
  return Boolean(
    (status?.configured.length ?? 0) > 0 ||
      (status?.live_devices.length ?? 0) > 0,
  );
}

export function OnboardingWizard() {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [licenseStatus, setLicenseStatus] = useState<LicenseStatus | null>(null);
  const [licenseError, setLicenseError] = useState<string | null>(null);
  const [licenseBusy, setLicenseBusy] = useState(false);
  const [licenseImporting, setLicenseImporting] = useState(false);
  const [env, setEnv] = useState<EnvHealth | null>(null);
  const [envBusy, setEnvBusy] = useState(false);
  const [state, setState] = useState<OnboardingState | null>(null);
  const [adbStatus, setAdbStatus] = useState<AdbStatus | null>(null);
  const [adbError, setAdbError] = useState<string | null>(null);
  const [deviceRefreshBusy, setDeviceRefreshBusy] = useState(false);
  const [botRunning, setBotRunning] = useState(false);
  const [startBusy, setStartBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshDeviceStep = useCallback(
    async ({
      showBusy = true,
      isCancelled = () => false,
    }: {
      showBusy?: boolean;
      isCancelled?: () => boolean;
    } = {}) => {
      if (showBusy) setDeviceRefreshBusy(true);
      try {
        const [nextState, adb] = await Promise.all([
          fetchOnboardingState().catch(() => null),
          readAdbStatus(),
        ]);
        if (isCancelled()) return;
        if (nextState) setState(nextState);
        setAdbStatus(adb.status);
        setAdbError(adb.error);
      } finally {
        if (showBusy && !isCancelled()) setDeviceRefreshBusy(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (!wizardSeen()) setOpen(true);
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      try {
        const [license, envHealth, st, bot, adb] = await Promise.all([
          readLicenseStatus(),
          fetchEnvHealth(),
          fetchOnboardingState(),
          fetchBotStatus(),
          readAdbStatus(),
        ]);
        if (cancelled) return;
        setLicenseStatus(license.status);
        setLicenseError(license.error);
        setEnv(envHealth);
        setState(st);
        setAdbStatus(adb.status);
        setAdbError(adb.error);
        const running = Boolean(bot.running);
        setBotRunning(running);
        if (!license.status?.active) setStep(STEP_LICENSE);
        else if (!envHealth.redis.ok) setStep(STEP_ENVIRONMENT);
        else if (!st.device_added_at) setStep(STEP_DEVICE);
        else setStep(STEP_BOT);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open || step !== STEP_DEVICE) return;
    let cancelled = false;
    const pull = () =>
      refreshDeviceStep({
        showBusy: false,
        isCancelled: () => cancelled,
      });
    void pull();
    const id = window.setInterval(() => {
      void pull();
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [open, refreshDeviceStep, step]);

  useEffect(() => {
    if (!open || step !== STEP_BOT) return;
    const id = window.setInterval(() => {
      fetchBotStatus()
        .then((s) => setBotRunning(Boolean(s.running)))
        .catch(() => {});
    }, 2000);
    return () => window.clearInterval(id);
  }, [open, step]);

  const recheckEnv = async () => {
    setEnvBusy(true);
    setError(null);
    try {
      setEnv(await fetchEnvHealth());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setEnvBusy(false);
    }
  };

  const recheckLicense = async () => {
    setLicenseBusy(true);
    setError(null);
    try {
      const license = await readLicenseStatus();
      setLicenseStatus(license.status);
      setLicenseError(license.error);
    } finally {
      setLicenseBusy(false);
    }
  };

  const importLicense = async (file: File) => {
    setLicenseImporting(true);
    setLicenseError(null);
    setError(null);
    try {
      const result = await importLicenseFile(file);
      setLicenseStatus(result.status);
      window.dispatchEvent(new Event("wos:license:updated"));
    } catch (err) {
      setLicenseError(err instanceof Error ? err.message : String(err));
    } finally {
      setLicenseImporting(false);
    }
  };

  const startBot = async () => {
    setStartBusy(true);
    setError(null);
    try {
      await startLocalBot();
      const s = await fetchBotStatus();
      setBotRunning(Boolean(s.running));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStartBusy(false);
    }
  };

  const close = () => {
    markWizardSeen();
    setOpen(false);
  };

  const licenseOk = Boolean(licenseStatus?.active);
  const envOk = Boolean(env?.redis.ok);
  const deviceOk = Boolean(state?.device_added_at) || hasCurrentDevice(adbStatus);
  const wizardComplete = step === STEP_BOT && botRunning;
  const canContinue =
    step === STEP_LICENSE
      ? licenseOk
      : step === STEP_ENVIRONMENT
        ? envOk
        : step === STEP_DEVICE
          ? deviceOk
          : true;

  return (
    <Dialog open={open} onClose={close} className="headless-dialog-root">
      <DialogBackdrop transition className="headless-dialog__backdrop" />
      <div className="headless-dialog__container">
        <DialogPanel transition className="headless-dialog__panel onboarding-wizard">
          <OnboardingConfetti active={wizardComplete} />
          <div className="onboarding-wizard__header">
            <DialogTitle className="headless-dialog__title">
              Welcome to Autopilot
            </DialogTitle>
            <button
              type="button"
              className="onboarding-wizard__skip"
              onClick={close}
            >
              Skip
            </button>
          </div>

          <ol className="onboarding-wizard__steps" aria-label="Wizard progress">
            {STEP_LABELS.map((label, i) => (
              <li
                key={label}
                className={[
                  "onboarding-wizard__step-pill",
                  i === step ? "is-active" : "",
                  i < step ? "is-done" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                <span className="onboarding-wizard__step-num">{i + 1}</span>
                <span>{label}</span>
              </li>
            ))}
          </ol>

          {error ? (
            <p className="onboarding-wizard__error">{error}</p>
          ) : null}

          <div className="headless-dialog__body onboarding-wizard__body">
            {step === STEP_LICENSE ? (
              <LicenseStep
                status={licenseStatus}
                error={licenseError}
                busy={licenseBusy}
                importing={licenseImporting}
                onRecheck={recheckLicense}
                onImport={importLicense}
              />
            ) : null}
            {step === STEP_ENVIRONMENT ? (
              <EnvironmentStep env={env} busy={envBusy} onRecheck={recheckEnv} />
            ) : null}
            {step === STEP_DEVICE ? (
              <DeviceStep
                deviceAddedAt={state?.device_added_at ?? null}
                adbStatus={adbStatus}
                adbError={adbError}
                refreshing={deviceRefreshBusy}
                onRefresh={refreshDeviceStep}
              />
            ) : null}
            {step === STEP_BOT ? (
              <StartBotStep
                running={botRunning}
                busy={startBusy}
                onStart={startBot}
              />
            ) : null}
          </div>

          <div className="headless-dialog__actions">
            {step > 0 ? (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setStep(step - 1)}
              >
                Back
              </button>
            ) : null}
            {step < LAST_STEP ? (
              <button
                type="button"
                className="btn-primary"
                disabled={!canContinue}
                onClick={() => setStep(step + 1)}
              >
                Next
              </button>
            ) : (
              <button type="button" className="btn-primary" onClick={close}>
                Finish
              </button>
            )}
          </div>
        </DialogPanel>
      </div>
    </Dialog>
  );
}

function LicenseStep({
  status,
  error,
  busy,
  importing,
  onRecheck,
  onImport,
}: {
  status: LicenseStatus | null;
  error: string | null;
  busy: boolean;
  importing: boolean;
  onRecheck: () => void;
  onImport: (file: File) => void;
}) {
  const active = Boolean(status?.active);
  const stateLabel = status ? status.state.replaceAll("_", " ") : "checking";
  const detail = error || status?.reason || "License status is being checked.";
  const uploadDisabled = busy || importing;
  return (
    <div className="onboarding-wizard__panel">
      {active ? (
        <p className="onboarding-wizard__success">
          ✓ License active{status?.tier ? ` · ${status.tier}` : ""}
        </p>
      ) : (
        <p>Activate a license before setting up the local worker and devices.</p>
      )}
      <div className="onboarding-license">
        <div className="onboarding-license__row">
          <span className="onboarding-license__label">Status</span>
          <span
            className={[
              "onboarding-license__state",
              active ? "is-active" : "is-blocked",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {active ? "active" : stateLabel}
          </span>
        </div>
        {status?.expires_at || status?.days_left != null ? (
          <div className="onboarding-license__row">
            <span className="onboarding-license__label">Expires</span>
            <span className="onboarding-license__value">
              {status.days_left != null
                ? `${status.days_left} day${status.days_left === 1 ? "" : "s"} left`
                : status.expires_at}
            </span>
          </div>
        ) : null}
        {!active ? <p className="onboarding-license__detail">{detail}</p> : null}
      </div>
      <div className="flex flex-wrap gap-2">
        <Link href="/license" className={active ? "btn-secondary" : "btn-primary"}>
          {active ? "Manage license" : "Open license"}
        </Link>
        <button
          type="button"
          className="btn-secondary"
          onClick={onRecheck}
          disabled={busy}
        >
          {busy ? "Checking…" : "Recheck"}
        </button>
      </div>
      {!active ? (
        <form
          className="onboarding-license-upload"
          onSubmit={(e) => e.preventDefault()}
        >
          <label className="onboarding-license-upload__label" htmlFor="onboarding-license-file">
            Import license file
          </label>
          <input
            id="onboarding-license-file"
            className="onboarding-license-upload__input"
            type="file"
            accept=".jwt,.licence,.license,.txt,text/plain,application/jwt"
            disabled={uploadDisabled}
            onChange={(e) => {
              const file = e.currentTarget.files?.[0];
              e.currentTarget.value = "";
              if (file) onImport(file);
            }}
          />
          <span className="onboarding-license-upload__hint">
            {importing ? "Importing…" : "JWT, licence, or text file"}
          </span>
          <a
            href={DISCORD_INVITE_URL}
            target="_blank"
            rel="noreferrer noopener"
            className="onboarding-license-upload__discord"
          >
            Get a trial license file on Discord
          </a>
        </form>
      ) : null}
    </div>
  );
}

function HealthRow({
  label,
  entry,
  required,
}: {
  label: string;
  entry: EnvHealthEntry | undefined;
  required: boolean;
}) {
  const status = !entry ? "loading" : entry.ok ? "ok" : required ? "fail" : "warn";
  const dot = status === "ok" ? "●" : status === "loading" ? "…" : status === "warn" ? "▲" : "✕";
  const detail = entry?.ok
    ? entry.version || (entry.latency_ms != null ? `${entry.latency_ms} ms` : "ok")
    : entry?.error || "checking…";
  return (
    <div className={`onboarding-health-row onboarding-health-row--${status}`}>
      <span className="onboarding-health-row__dot" aria-hidden>
        {dot}
      </span>
      <span className="onboarding-health-row__label">{label}</span>
      <span className="onboarding-health-row__detail">{detail}</span>
    </div>
  );
}

function EnvironmentStep({
  env,
  busy,
  onRecheck,
}: {
  env: EnvHealth | null;
  busy: boolean;
  onRecheck: () => void;
}) {
  return (
    <div className="onboarding-wizard__panel">
      <p>
        Verifying that Redis, Tesseract, and ADB are reachable. Redis is
        required; the other two are checked again at runtime, so warnings
        here are not fatal.
      </p>
      <div className="onboarding-health-rows">
        <HealthRow label="Redis" entry={env?.redis} required />
        <HealthRow label="Tesseract OCR" entry={env?.tesseract} required={false} />
        <HealthRow label="ADB" entry={env?.adb} required={false} />
      </div>
      <button
        type="button"
        className="btn-secondary"
        onClick={onRecheck}
        disabled={busy}
      >
        {busy ? "Checking…" : "Recheck"}
      </button>
    </div>
  );
}

function DeviceStep({
  deviceAddedAt,
  adbStatus,
  adbError,
  refreshing,
  onRefresh,
}: {
  deviceAddedAt: string | null;
  adbStatus: AdbStatus | null;
  adbError: string | null;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const deviceReady = Boolean(deviceAddedAt) || hasCurrentDevice(adbStatus);
  if (deviceReady) {
    return (
      <div className="onboarding-wizard__panel">
        <p className="onboarding-wizard__success">
          ✓ At least one device is detected.
        </p>
        <p>
          Continue to start the bot, or open ADB settings to adjust capture and
          input backends.
        </p>
        <div className="flex flex-wrap gap-2">
          <Link href="/adb" className="btn-secondary">
            Open ADB settings
          </Link>
          <button
            type="button"
            className="btn-secondary"
            onClick={onRefresh}
            disabled={refreshing}
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        <CurrentDevices status={adbStatus} error={adbError} />
      </div>
    );
  }
  return (
    <div className="onboarding-wizard__panel">
      <p>
        Connect an Android emulator (720×1280, 320 DPI) or a physical device
        over ADB. Once it appears below, you can continue.
      </p>
      <div className="flex flex-wrap gap-2">
        <Link href="/adb" className="btn-primary">
          Open ADB settings
        </Link>
        <button
          type="button"
          className="btn-secondary"
          onClick={onRefresh}
          disabled={refreshing}
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <CurrentDevices status={adbStatus} error={adbError} />
      <p className="onboarding-wizard__hint">
        This page polls every 3 seconds — once you add a device, it will
        light up automatically.
      </p>
    </div>
  );
}

function CurrentDevices({
  status,
  error,
}: {
  status: AdbStatus | null;
  error: string | null;
}) {
  if (error) {
    return (
      <div className="onboarding-devices" aria-label="Current devices">
        <div className="onboarding-devices__head">
          <span>Current devices</span>
          <span className="onboarding-devices__badge onboarding-devices__badge--warn">
            scan failed
          </span>
        </div>
        <p className="onboarding-devices__empty">{error}</p>
      </div>
    );
  }
  if (!status) {
    return (
      <div className="onboarding-devices" aria-label="Current devices">
        <div className="onboarding-devices__head">
          <span>Current devices</span>
          <span className="onboarding-devices__badge">checking</span>
        </div>
      </div>
    );
  }

  const liveSerials = new Set(
    status.live_devices.flatMap((d) =>
      adbSerialAliases(d.serial, d.canonical_serial),
    ),
  );
  const configured = status.configured.slice(0, 4);
  const liveOnly = status.live_devices
    .filter(
      (d) =>
        !status.configured.some((c) =>
          adbSerialMatches(c.adb_serial, d.serial, d.canonical_serial),
        ),
    )
    .slice(0, 4);

  return (
    <div className="onboarding-devices" aria-label="Current devices">
      <div className="onboarding-devices__head">
        <span>Current devices</span>
        <span className="onboarding-devices__counts">
          <span className="onboarding-devices__badge">
            Configured {status.configured.length}
          </span>
          <span className="onboarding-devices__badge">
            Live {status.live_devices.length}
          </span>
        </span>
      </div>
      {status.scan_error ? (
        <p className="onboarding-devices__empty">Scan: {status.scan_error}</p>
      ) : null}
      {configured.length ? (
        <ul className="onboarding-devices__list">
          {configured.map((d) => {
            const isLive = adbSerialAliases(d.adb_serial).some((alias) =>
              liveSerials.has(alias),
            );
            return (
              <li
                key={`${d.name}-${d.adb_serial}`}
                className="onboarding-devices__row"
              >
                <span className="onboarding-devices__name">
                  {d.name || d.adb_serial || "Unnamed device"}
                </span>
                <code className="onboarding-devices__serial">
                  {d.adb_serial || "no serial"}
                </code>
                <span
                  className={[
                    "onboarding-devices__state",
                    isLive ? "is-live" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                >
                  {isLive ? "live" : "offline"}
                </span>
              </li>
            );
          })}
        </ul>
      ) : liveOnly.length ? (
        <ul className="onboarding-devices__list">
          {liveOnly.map((d) => (
            <li key={d.serial} className="onboarding-devices__row">
              <span className="onboarding-devices__name">Detected by ADB</span>
              <code className="onboarding-devices__serial">{d.serial}</code>
              <span className="onboarding-devices__state is-live">live</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="onboarding-devices__empty">No current devices found.</p>
      )}
    </div>
  );
}

function StartBotStep({
  running,
  busy,
  onStart,
}: {
  running: boolean;
  busy: boolean;
  onStart: () => void;
}) {
  if (running) {
    return (
      <div className="onboarding-wizard__panel">
        <p className="onboarding-wizard__success">✓ The bot is running.</p>
        <p>
          Open the Overview page to see live fleet status, or finish the
          wizard.
        </p>
      </div>
    );
  }
  return (
    <div className="onboarding-wizard__panel">
      <p>
        Start the bot worker. It will pick up devices from SQLite and begin
        executing scheduled scenarios.
      </p>
      <button
        type="button"
        className="btn-primary"
        onClick={onStart}
        disabled={busy}
      >
        {busy ? "Starting…" : "Start bot"}
      </button>
    </div>
  );
}
