import { AppMenu, AppRadioGroup } from "@/components/headless";
import { INPUT_BACKEND_OPTIONS } from "@/lib/adb/types";
import type { AdbState } from "./useAdbState";

export function ConfiguredDevicesTable({ adb }: { adb: AdbState }) {
  const { status, configuredFiltered, sectionCount, busy, onBackendChange, setDeleteDeviceName, clearFilters } =
    adb;
  if (!status) return null;

  return (
    <section className="panel panel--spaced">
      <h2>Configured ({sectionCount(configuredFiltered.length, status.configured.length)})</h2>
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
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {configuredFiltered.length === 0 && status.configured.length > 0 ? (
              <tr>
                <td colSpan={7}>
                  <span className="muted">
                    No devices match the current filters.{" "}
                    <button
                      type="button"
                      className="cursor-pointer border-0 bg-transparent p-0 text-sky-400 underline-offset-2 hover:underline"
                      onClick={clearFilters}
                    >
                      Clear filters
                    </button>
                  </span>
                </td>
              </tr>
            ) : null}
            {configuredFiltered.map((d) => (
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
                    onChange={(v) => onBackendChange(d.adb_serial, "screenshot_backend", v)}
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
                    onChange={(v) => onBackendChange(d.adb_serial, "input_backend", v)}
                    options={INPUT_BACKEND_OPTIONS}
                  />
                </td>
                <td>
                  <AppMenu
                    items={[
                      {
                        label: "Remove",
                        disabled: busy || !d.name,
                        title: "Remove this configured device from the fleet registry",
                        onClick: () => setDeleteDeviceName(d.name),
                      },
                    ]}
                    ariaLabel={`Open actions for ${d.name || d.adb_serial}`}
                    buttonTitle="Actions"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
