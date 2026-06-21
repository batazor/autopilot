import { AppMenu, AppPopover, type AppMenuItem } from "@/components/headless";
import { Button, EmptyState, Icon } from "@/components/ui";
import { adbSerialMatches } from "@/lib/adb-serial";
import { DetectedGames, ScrcpyStatusCell } from "./cells";
import { ScanPortRangeControls } from "./ScanPortRangeControls";
import type { AdbState } from "./useAdbState";

export function LiveDevicesTable({ adb }: { adb: AdbState }) {
  const {
    status,
    liveFiltered,
    scrcpy,
    registeringSerial,
    installingScrcpy,
    resettingSerial,
    busy,
    onRegisterDevice,
    onUseOnlyDevice,
    onInstallScrcpy,
    onResetDisplay,
    sectionCount,
    clearFilters,
    portRange,
    updatePortRange,
    scanning,
    refreshScanAndRegister,
    setManualDeviceOpen,
  } = adb;
  if (!status) return null;

  return (
    <section className="panel panel--spaced">
      <h2>Live devices ({sectionCount(liveFiltered.length, status.live_devices.length)})</h2>
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
            {liveFiltered.length === 0 && status.live_devices.length > 0 ? (
              <tr>
                <td colSpan={5}>
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
            {status.live_devices.length === 0 ? (
              <tr>
                <td colSpan={5}>
                  <EmptyState
                    icon="adb"
                    title="No live ADB devices found"
                    description={`The scan checked ports ${portRange.start}-${portRange.end} with step ${portRange.step}. If your emulator uses another port, adjust the range and rescan, or add the ADB serial manually from adb devices.`}
                    action={
                      <div className="adb-empty-actions">
                        <AppPopover
                          ariaLabel="Configure TCP scan port range"
                          buttonTitle="Configure TCP scan port range"
                          panelClassName="headless-popover__panel w-72 p-3"
                          trigger="Configure scan"
                        >
                          {({ close }) => (
                            <ScanPortRangeControls
                              portRange={portRange}
                              updatePortRange={updatePortRange}
                              scanning={scanning}
                              onRescan={() => void refreshScanAndRegister()}
                              onClose={close}
                            />
                          )}
                        </AppPopover>
                        <Button variant="primary" onClick={() => setManualDeviceOpen(true)} disabled={busy}>
                          <Icon name="plus" size="sm" />
                          Add manually
                        </Button>
                      </div>
                    }
                  />
                </td>
              </tr>
            ) : null}
            {liveFiltered.map((d) => {
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
                  label: registeringSerial === d.serial ? "Updating..." : "Use only this device",
                  disabled: busy,
                  title: "Remove other configured devices and keep this ADB serial",
                  onClick: () => onUseOnlyDevice(d.serial),
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
                  label: resettingSerial === d.serial ? "Resetting..." : "Reset screen",
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
                  <td>
                    <DetectedGames games={d.detected_games} />
                  </td>
                  <td>
                    <ScrcpyStatusCell entry={sc} />
                  </td>
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
  );
}
