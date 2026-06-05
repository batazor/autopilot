"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { AppMenu, AppRadioGroup, type AppMenuItem } from "@/components/headless";
import {
  fetchAdbStatus,
  fetchScrcpyStatus,
  installScrcpy,
  registerAdbDevice,
  resetAdbDeviceDisplay,
  updateDeviceBackend,
} from "@/lib/api";
import { adbSerialMatches } from "@/lib/adb-serial";
import type {
  AdbDetectedGame,
  ScrcpyInstallResult,
  ScrcpyStatus,
} from "@/lib/config-pages";

const INPUT_BACKEND_OPTIONS = [
  { value: "", label: "auto (scrcpy)" },
  { value: "adb", label: "adb" },
  { value: "scrcpy", label: "scrcpy" },
];

type CellEntry<T> = T | { error: string } | undefined;

function renderBinaryCell(entry: CellEntry<ScrcpyStatus>) {
  if (entry === undefined) return <span className="muted">checking…</span>;
  if ("error" in entry) {
    return (
      <span className="error-text" title={entry.error}>
        error
      </span>
    );
  }
  const detail = [entry.abi, entry.sdk ? `android-${entry.sdk}` : null]
    .filter(Boolean)
    .join(" · ");
  if (entry.installed) {
    return (
      <span className="success-text">
        installed{detail && ` · ${detail}`}
      </span>
    );
  }
  return (
    <span className="muted" title={entry.last_error ?? "missing"}>
      not installed{detail && ` · ${detail}`}
    </span>
  );
}

function scrcpyInstallNote(result?: ScrcpyInstallResult | null): string {
  if (!result) return "";
  if (result.ok || result.installed) return " Scrcpy server installed.";
  return ` Scrcpy auto-install failed: ${result.last_error ?? "unknown error"}.`;
}

function gameBadgeLabel(game: AdbDetectedGame): string {
  if (game.id === "wos") return "WOS";
  return game.label || game.id.toUpperCase();
}

function renderDetectedGames(games?: AdbDetectedGame[]) {
  if (!games?.length) return <span className="muted">—</span>;
  return (
    <span className="flex flex-wrap gap-1.5">
      {games.map((game) => (
        <span
          key={`${game.id}-${game.package}`}
          className={`status-pill ${game.running ? "pill-live" : "pill-busy"}`}
          title={`${game.label} (${game.package}) · ${game.running ? "running" : "installed"}`}
        >
          <span>{gameBadgeLabel(game)}</span>
          {game.beta && (
            <span className="rounded-full border border-current/40 px-1 py-0 text-[9px] font-semibold uppercase opacity-90">
              beta
            </span>
          )}
        </span>
      ))}
    </span>
  );
}

export default function AdbPage() {
  const [status, setStatus] = useState<Awaited<ReturnType<typeof fetchAdbStatus>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [resettingSerial, setResettingSerial] = useState<string | null>(null);
  const [scrcpy, setScrcpy] = useState<Record<string, CellEntry<ScrcpyStatus>>>({});
  const [installingScrcpy, setInstallingScrcpy] = useState<string | null>(null);
  const [registeringSerial, setRegisteringSerial] = useState<string | null>(null);

  const loadProbes = useCallback(async (serials: string[]) => {
    const scrcpyResults = await Promise.all(
      serials.map(async (serial) => {
        try {
          return [serial, await fetchScrcpyStatus(serial)] as const;
        } catch (e) {
          return [serial, { error: e instanceof Error ? e.message : String(e) }] as const;
        }
      }),
    );
    setScrcpy(Object.fromEntries(scrcpyResults));
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      const s = await fetchAdbStatus();
      setStatus(s);
      void loadProbes(s.live_devices.map((d) => d.serial));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [loadProbes]);

  useEffect(() => {
    load();
  }, [load]);

  const onResetDisplay = async (serial: string) => {
    setError(null);
    setSuccess(null);
    setResettingSerial(serial);
    try {
      const out = await resetAdbDeviceDisplay(serial);
      const parts = [out.wm_size, out.wm_density].filter(Boolean);
      setSuccess(
        parts.length
          ? `Screen reset on ${serial}: ${parts.join(" · ")}`
          : `Screen reset on ${serial}`,
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setResettingSerial(null);
    }
  };

  const onRegisterDevice = async (serial: string) => {
    setError(null);
    setSuccess(null);
    setRegisteringSerial(serial);
    try {
      const out = await registerAdbDevice(serial);
      const installNote = scrcpyInstallNote(out.scrcpy_install);
      setSuccess(
        out.created
          ? `Registered ${out.adb_serial} as ${out.name}.${installNote} Its worker starts automatically while the bot is running.`
          : `${out.adb_serial} is already registered as ${out.name}.${installNote}`,
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRegisteringSerial(null);
    }
  };

  const onInstallScrcpy = async (serial: string) => {
    setError(null);
    setSuccess(null);
    setInstallingScrcpy(serial);
    try {
      const out = await installScrcpy(serial);
      if (out.installed) {
        setSuccess(`Scrcpy server installed on ${serial} (${out.abi})`);
      } else {
        setError(`Scrcpy install on ${serial} failed: ${out.last_error ?? "unknown error"}`);
      }
      setScrcpy((prev) => ({ ...prev, [serial]: out }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setInstallingScrcpy(null);
    }
  };

  const [savingBackend, setSavingBackend] = useState<string | null>(null);

  const onBackendChange = async (
    serial: string,
    field: "screenshot_backend" | "input_backend",
    value: string,
  ) => {
    setError(null);
    setSuccess(null);
    setSavingBackend(`${serial}:${field}`);
    try {
      const out = await updateDeviceBackend(serial, { [field]: value });
      const label = field === "screenshot_backend" ? "screen capture" : "input";
      const shown = value || "auto";
      const installNote = scrcpyInstallNote(out.scrcpy_install);
      setSuccess(
        out.restart_required
          ? `${label} backend on ${serial} → ${shown}.${installNote} Restart the bot to apply.`
          : `${label} backend on ${serial} → ${shown}.${installNote}`,
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingBackend(null);
    }
  };

  const busy =
    installingScrcpy !== null ||
    registeringSerial !== null ||
    resettingSerial !== null ||
    savingBackend !== null;

  const unregistered = status
    ? status.live_devices.filter(
        (d) =>
          !status.configured.some((c) =>
            adbSerialMatches(c.adb_serial, d.serial, d.canonical_serial),
          ),
      )
    : [];

  return (
    <>
      <PageHeader title="ADB">
        <p className="muted">
          Configured devices vs live adb scan. Reset display clears{" "}
          <code>wm size</code> / <code>wm density</code> overrides on the device.
          {" "}<strong>Scrcpy</strong> = one server process per device delivering H.264 frames;
          every device uses it by default for both capture and input.
          Scrcpy input works on unrooted devices and auto-pushes <code>scrcpy-server.jar</code> when enabled.
          Select the backend in the dropdowns below to opt in per device.
        </p>
      </PageHeader>
      <div className="toolbar mb-4">
        <button type="button" className="btn-secondary" onClick={load}>
          Refresh scan
        </button>
      </div>
      {error && <p className="error-banner mb-4">{error}</p>}
      {success && <p className="success-banner mb-4">{success}</p>}
      {status && (
        <>
          {unregistered.length > 0 && (
            <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
              <span>
                <strong>
                  {unregistered.length === 1
                    ? "1 live device isn't registered"
                    : `${unregistered.length} live devices aren't registered`}
                </strong>{" "}
                — register {unregistered.length === 1 ? "it" : "them"} to run the
                bot. The worker starts automatically while the bot is running.
              </span>
              {unregistered.map((d) => (
                <button
                  key={d.serial}
                  type="button"
                  className="btn-secondary"
                  disabled={busy}
                  onClick={() => onRegisterDevice(d.serial)}
                >
                  {registeringSerial === d.serial
                    ? "Registering…"
                    : `Register ${d.serial}`}
                </button>
              ))}
            </div>
          )}
          <dl className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-wos-border-subtle/70 bg-wos-panel-raised/40 px-3 py-2 text-xs">
            <div className="flex items-center gap-2">
              <dt className="font-semibold uppercase tracking-wide text-wos-text-muted">
                adb
              </dt>
              <dd className="m-0">
                <code className="text-wos-text">{status.adb_executable}</code>
              </dd>
            </div>
            <div className="flex items-center gap-2">
              <dt className="font-semibold uppercase tracking-wide text-wos-text-muted">
                State DB
              </dt>
              <dd className="m-0">
                <code className="text-wos-text">{status.devices_yaml}</code>
              </dd>
            </div>
          </dl>
          {status.scan_error && (
            <p className="error-banner mb-4">Scan: {status.scan_error}</p>
          )}
          <section className="panel panel--spaced">
            <h2>Configured ({status.configured.length})</h2>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>ADB serial</th>
                    <th>Instance</th>
                    <th>Window title</th>
                    <th>Capture</th>
                    <th>Input</th>
                  </tr>
                </thead>
                <tbody>
                  {status.configured.map((d) => (
                    <tr key={`${d.name}-${d.adb_serial}`}>
                      <td>{d.name || "—"}</td>
                      <td>
                        <code>{d.adb_serial || "—"}</code>
                      </td>
                      <td>{d.instance_id || "—"}</td>
                      <td>{d.bluestacks_window_title || "—"}</td>
                      <td>
                        <AppRadioGroup
                          aria-label="Screenshot backend"
                          value={d.screenshot_backend}
                          disabled={busy || !d.adb_serial}
                          onChange={(v) =>
                            onBackendChange(d.adb_serial, "screenshot_backend", v)
                          }
                          options={[
                            {
                              value: "",
                              label: `auto (${d.screenshot_backend_effective || "scrcpy"})`,
                            },
                            { value: "adb", label: "adb" },
                            { value: "scrcpy", label: "scrcpy" },
                          ]}
                        />
                      </td>
                      <td>
                        <AppRadioGroup
                          aria-label="Input backend"
                          value={d.input_backend}
                          disabled={busy || !d.adb_serial}
                          onChange={(v) =>
                            onBackendChange(d.adb_serial, "input_backend", v)
                          }
                          options={INPUT_BACKEND_OPTIONS}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="panel panel--spaced">
            <h2>Live devices ({status.live_devices.length})</h2>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Serial</th>
                    <th>Line</th>
                    <th>Game</th>
                    <th>Scrcpy</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {status.live_devices.map((d) => {
                    const sc = scrcpy[d.serial];
                    const scInstalled = sc && !("error" in sc) && sc.installed;
                    const isConfigured = status.configured.some((c) =>
                      adbSerialMatches(c.adb_serial, d.serial, d.canonical_serial),
                    );
                    const actionItems: AppMenuItem[] = [
                      {
                        label: isConfigured
                          ? "Registered"
                          : registeringSerial === d.serial
                            ? "Registering..."
                            : "Register",
                        disabled: busy || isConfigured,
                        title: isConfigured
                          ? "Already registered in the fleet"
                          : "Add this live ADB device to the fleet registry",
                        onClick: () => onRegisterDevice(d.serial),
                      },
                      {
                        label:
                          installingScrcpy === d.serial
                            ? "Installing..."
                            : scInstalled
                              ? "Reinstall scrcpy"
                              : "Install scrcpy",
                        disabled: busy,
                        title: "Download scrcpy-server.jar from Genymobile/scrcpy and push to /data/local/tmp",
                        onClick: () => onInstallScrcpy(d.serial),
                      },
                      { kind: "separator" },
                      {
                        label:
                          resettingSerial === d.serial
                            ? "Resetting..."
                            : "Reset screen",
                        disabled: busy,
                        title: "adb shell wm size reset && wm density reset",
                        onClick: () => onResetDisplay(d.serial),
                      },
                    ];
                    return (
                      <tr key={d.serial}>
                        <td>
                          <code>{d.serial}</code>
                          {!isConfigured && (
                            <span
                              className="ml-2 rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-200"
                              title="Not in the fleet registry — no worker will run for it"
                            >
                              unregistered
                            </span>
                          )}
                        </td>
                        <td className="muted">{d.line}</td>
                        <td>{renderDetectedGames(d.detected_games)}</td>
                        <td>{renderBinaryCell(sc)}</td>
                        <td>
                          <AppMenu
                            items={actionItems}
                            ariaLabel={`Open actions for ${d.serial}`}
                            buttonTitle="Actions"
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}
