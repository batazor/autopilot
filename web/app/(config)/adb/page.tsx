"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { fetchAdbStatus, resetAdbDeviceDisplay } from "@/lib/api";

export default function AdbPage() {
  const [status, setStatus] = useState<Awaited<ReturnType<typeof fetchAdbStatus>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [resettingSerial, setResettingSerial] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setStatus(await fetchAdbStatus());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

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

  return (
    <>
      <PageHeader title="ADB">
        <p className="muted">
          Configured devices vs live adb scan. Reset display clears{" "}
          <code>wm size</code> / <code>wm density</code> overrides on the device.
        </p>
      </PageHeader>
      <div className="toolbar">
        <button type="button" className="btn-secondary" onClick={load}>
          Refresh scan
        </button>
      </div>
      {error && <p className="error-banner">{error}</p>}
      {success && <p className="success-banner">{success}</p>}
      {status && (
        <>
          <p className="muted">
            <code>{status.adb_executable}</code> · {status.devices_yaml}
          </p>
          {status.scan_error && (
            <p className="error-banner">Scan: {status.scan_error}</p>
          )}
          <section className="panel">
            <h2>Configured ({status.configured.length})</h2>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>ADB serial</th>
                    <th>Instance</th>
                    <th>Window title</th>
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
                    <th>Display</th>
                  </tr>
                </thead>
                <tbody>
                  {status.live_devices.map((d) => (
                    <tr key={d.serial}>
                      <td>
                        <code>{d.serial}</code>
                      </td>
                      <td className="muted">{d.line}</td>
                      <td>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={resettingSerial !== null}
                          title="adb shell wm size reset && wm density reset"
                          onClick={() => onResetDisplay(d.serial)}
                        >
                          {resettingSerial === d.serial
                            ? "Resetting…"
                            : "Reset screen"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}
