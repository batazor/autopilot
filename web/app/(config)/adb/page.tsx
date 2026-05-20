"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { fetchAdbStatus } from "@/lib/api";
import type { AdbStatus } from "@/lib/config-pages";

export default function AdbPage() {
  const [status, setStatus] = useState<AdbStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <>
      <PageHeader title="ADB"><p className="muted">Configured devices vs live adb scan.</p></PageHeader>
      <div className="toolbar">
        <button type="button" className="btn-secondary" onClick={load}>
          Refresh scan
        </button>
      </div>
      {error && <p className="error-banner">{error}</p>}
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
                  </tr>
                </thead>
                <tbody>
                  {status.live_devices.map((d) => (
                    <tr key={d.serial}>
                      <td>
                        <code>{d.serial}</code>
                      </td>
                      <td className="muted">{d.line}</td>
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
