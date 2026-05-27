"use client";

import {
  Dialog,
  DialogBackdrop,
  DialogPanel,
  DialogTitle,
} from "@headlessui/react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { fetchBotStatus, startLocalBot } from "@/lib/api";
import {
  type EnvHealth,
  type EnvHealthEntry,
  fetchEnvHealth,
  fetchOnboardingState,
  markWizardSeen,
  type OnboardingState,
  wizardSeen,
} from "@/lib/onboarding";

const STEP_LABELS = ["Environment", "Add device", "Start bot"] as const;

export function OnboardingWizard() {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [env, setEnv] = useState<EnvHealth | null>(null);
  const [envBusy, setEnvBusy] = useState(false);
  const [state, setState] = useState<OnboardingState | null>(null);
  const [botRunning, setBotRunning] = useState(false);
  const [startBusy, setStartBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!wizardSeen()) setOpen(true);
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      try {
        const [envHealth, st, bot] = await Promise.all([
          fetchEnvHealth(),
          fetchOnboardingState(),
          fetchBotStatus(),
        ]);
        if (cancelled) return;
        setEnv(envHealth);
        setState(st);
        const running = Boolean(bot.running);
        setBotRunning(running);
        if (!envHealth.redis.ok) setStep(0);
        else if (!st.device_added_at) setStep(1);
        else setStep(2);
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
    if (!open || step !== 1) return;
    const id = window.setInterval(() => {
      fetchOnboardingState()
        .then(setState)
        .catch(() => {});
    }, 3000);
    return () => window.clearInterval(id);
  }, [open, step]);

  useEffect(() => {
    if (!open || step !== 2) return;
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

  const step1Ok = Boolean(env?.redis.ok);
  const step2Ok = Boolean(state?.device_added_at);

  return (
    <Dialog open={open} onClose={close} className="headless-dialog-root">
      <DialogBackdrop transition className="headless-dialog__backdrop" />
      <div className="headless-dialog__container">
        <DialogPanel transition className="headless-dialog__panel onboarding-wizard">
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
            {step === 0 ? (
              <EnvironmentStep env={env} busy={envBusy} onRecheck={recheckEnv} />
            ) : null}
            {step === 1 ? (
              <DeviceStep
                deviceAddedAt={state?.device_added_at ?? null}
                onRefresh={() => {
                  fetchOnboardingState().then(setState).catch(() => {});
                }}
              />
            ) : null}
            {step === 2 ? (
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
            {step < 2 ? (
              <button
                type="button"
                className="btn-primary"
                disabled={step === 0 ? !step1Ok : !step2Ok}
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
  onRefresh,
}: {
  deviceAddedAt: string | null;
  onRefresh: () => void;
}) {
  if (deviceAddedAt) {
    return (
      <div className="onboarding-wizard__panel">
        <p className="onboarding-wizard__success">
          ✓ At least one device is configured.
        </p>
        <p>
          You can manage all devices on the ADB page. Continue to start the
          bot.
        </p>
      </div>
    );
  }
  return (
    <div className="onboarding-wizard__panel">
      <p>
        Connect an Android emulator (720×1280, 320 DPI) or a physical device
        over ADB, then register it on the ADB page.
      </p>
      <div className="flex flex-wrap gap-2">
        <Link href="/adb" className="btn-primary">
          Open ADB settings
        </Link>
        <button type="button" className="btn-secondary" onClick={onRefresh}>
          Refresh
        </button>
      </div>
      <p className="onboarding-wizard__hint">
        This page polls every 3 seconds — once you add a device, it will
        light up automatically.
      </p>
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
