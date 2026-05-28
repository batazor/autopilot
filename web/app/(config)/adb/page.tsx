"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { AppRadioGroup } from "@/components/headless";
import {
  fetchAdbStatus,
  fetchMinicapStatus,
  fetchMinitouchStatus,
  fetchScrcpyStatus,
  installMinicap,
  installMinitouch,
  installScrcpy,
  resetAdbDeviceDisplay,
  updateDeviceBackend,
} from "@/lib/api";
import type { MinicapStatus, MinitouchStatus, ScrcpyStatus } from "@/lib/config-pages";

const INPUT_BACKEND_OPTIONS = [
  { value: "", label: "auto (adb)" },
  { value: "adb", label: "adb" },
  { value: "minitouch", label: "minitouch" },
  { value: "scrcpy", label: "scrcpy" },
];

type CellEntry<T> = T | { error: string } | undefined;

function renderBinaryCell(entry: CellEntry<MinicapStatus | MinitouchStatus | ScrcpyStatus>) {
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

export default function AdbPage() {
  const [status, setStatus] = useState<Awaited<ReturnType<typeof fetchAdbStatus>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [resettingSerial, setResettingSerial] = useState<string | null>(null);
  const [minicap, setMinicap] = useState<Record<string, CellEntry<MinicapStatus>>>({});
  const [minitouch, setMinitouch] = useState<Record<string, CellEntry<MinitouchStatus>>>({});
  const [scrcpy, setScrcpy] = useState<Record<string, CellEntry<ScrcpyStatus>>>({});
  const [installingMinicap, setInstallingMinicap] = useState<string | null>(null);
  const [installingMinitouch, setInstallingMinitouch] = useState<string | null>(null);
  const [installingScrcpy, setInstallingScrcpy] = useState<string | null>(null);

  const loadProbes = useCallback(async (serials: string[]) => {
    const [capResults, touchResults, scrcpyResults] = await Promise.all([
      Promise.all(
        serials.map(async (serial) => {
          try {
            return [serial, await fetchMinicapStatus(serial)] as const;
          } catch (e) {
            return [serial, { error: e instanceof Error ? e.message : String(e) }] as const;
          }
        }),
      ),
      Promise.all(
        serials.map(async (serial) => {
          try {
            return [serial, await fetchMinitouchStatus(serial)] as const;
          } catch (e) {
            return [serial, { error: e instanceof Error ? e.message : String(e) }] as const;
          }
        }),
      ),
      Promise.all(
        serials.map(async (serial) => {
          try {
            return [serial, await fetchScrcpyStatus(serial)] as const;
          } catch (e) {
            return [serial, { error: e instanceof Error ? e.message : String(e) }] as const;
          }
        }),
      ),
    ]);
    setMinicap(Object.fromEntries(capResults));
    setMinitouch(Object.fromEntries(touchResults));
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

  const onInstallMinicap = async (serial: string) => {
    setError(null);
    setSuccess(null);
    setInstallingMinicap(serial);
    try {
      const out = await installMinicap(serial);
      if (out.installed) {
        setSuccess(`Minicap installed on ${serial} (${out.abi} / android-${out.sdk})`);
      } else {
        setError(`Minicap install on ${serial} failed: ${out.last_error ?? "unknown error"}`);
      }
      setMinicap((prev) => ({ ...prev, [serial]: out }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setInstallingMinicap(null);
    }
  };

  const onInstallMinitouch = async (serial: string) => {
    setError(null);
    setSuccess(null);
    setInstallingMinitouch(serial);
    try {
      const out = await installMinitouch(serial);
      if (out.installed) {
        setSuccess(`Minitouch installed on ${serial} (${out.abi})`);
      } else {
        setError(`Minitouch install on ${serial} failed: ${out.last_error ?? "unknown error"}`);
      }
      setMinitouch((prev) => ({ ...prev, [serial]: out }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setInstallingMinitouch(null);
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
      setSuccess(
        out.restart_required
          ? `${label} backend on ${serial} → ${shown}. Restart the bot to apply.`
          : `${label} backend on ${serial} → ${shown}`,
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingBackend(null);
    }
  };

  const busy =
    installingMinicap !== null ||
    installingMinitouch !== null ||
    installingScrcpy !== null ||
    resettingSerial !== null ||
    savingBackend !== null;

  return (
    <>
      <PageHeader title="ADB">
        <p className="muted">
          Configured devices vs live adb scan. Reset display clears{" "}
          <code>wm size</code> / <code>wm density</code> overrides on the device.
          {" "}<strong>Minicap</strong> = fast screen capture (~15-40 ms/frame; physical → minicap by default, emulator → quartz).
          {" "}<strong>Minitouch</strong> = fast taps/swipes (~5-20 ms each), but needs <code>/dev/input</code> access
          (rooted devices or accessible emulators only) — input defaults to <code>adb</code> for universal compatibility.
          {" "}<strong>Scrcpy</strong> = one server process per device delivering both H.264 frames and touch events;
          works on any unrooted device, auto-pushes <code>scrcpy-server.jar</code> on first start.
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
                              label: `auto (${d.screenshot_backend_effective || "quartz"})`,
                            },
                            { value: "quartz", label: "quartz" },
                            { value: "adb", label: "adb" },
                            { value: "minicap", label: "minicap" },
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
                    <th>Minicap</th>
                    <th>Minitouch</th>
                    <th>Scrcpy</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {status.live_devices.map((d) => {
                    const cap = minicap[d.serial];
                    const touch = minitouch[d.serial];
                    const sc = scrcpy[d.serial];
                    const capInstalled = cap && !("error" in cap) && cap.installed;
                    const touchInstalled = touch && !("error" in touch) && touch.installed;
                    const scInstalled = sc && !("error" in sc) && sc.installed;
                    return (
                      <tr key={d.serial}>
                        <td>
                          <code>{d.serial}</code>
                        </td>
                        <td className="muted">{d.line}</td>
                        <td>{renderBinaryCell(cap)}</td>
                        <td>{renderBinaryCell(touch)}</td>
                        <td>{renderBinaryCell(sc)}</td>
                        <td>
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={busy}
                            title="Download minicap prebuilt and push to /data/local/tmp"
                            onClick={() => onInstallMinicap(d.serial)}
                            style={{ marginRight: 6 }}
                          >
                            {installingMinicap === d.serial
                              ? "Installing…"
                              : capInstalled
                                ? "Reinstall minicap"
                                : "Install minicap"}
                          </button>
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={busy}
                            title="Download minitouch prebuilt and push to /data/local/tmp"
                            onClick={() => onInstallMinitouch(d.serial)}
                            style={{ marginRight: 6 }}
                          >
                            {installingMinitouch === d.serial
                              ? "Installing…"
                              : touchInstalled
                                ? "Reinstall minitouch"
                                : "Install minitouch"}
                          </button>
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={busy}
                            title="Download scrcpy-server.jar from Genymobile/scrcpy and push to /data/local/tmp"
                            onClick={() => onInstallScrcpy(d.serial)}
                            style={{ marginRight: 6 }}
                          >
                            {installingScrcpy === d.serial
                              ? "Installing…"
                              : scInstalled
                                ? "Reinstall scrcpy"
                                : "Install scrcpy"}
                          </button>
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={busy}
                            title="adb shell wm size reset && wm density reset"
                            onClick={() => onResetDisplay(d.serial)}
                          >
                            {resettingSerial === d.serial
                              ? "Resetting…"
                              : "Reset screen"}
                          </button>
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
