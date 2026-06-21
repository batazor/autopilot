"use client";

import { PageHeader } from "@/components/PageHeader";
import { AppConfirmDialog } from "@/components/headless";
import { Button } from "@/components/ui";
import { ActivityLog } from "./ActivityLog";
import { AdbFilterBar } from "./AdbFilterBar";
import { ConfiguredDevicesTable } from "./ConfiguredDevicesTable";
import { LiveDevicesTable } from "./LiveDevicesTable";
import { ManualDeviceDialog } from "./ManualDeviceDialog";
import { useAdbState } from "./useAdbState";

export function AdbView() {
  const adb = useAdbState();
  const {
    status,
    error,
    success,
    unregistered,
    busy,
    registeringSerial,
    onRegisterDevice,
    deleteDeviceName,
    setDeleteDeviceName,
    deletingDevice,
    onDeleteDevice,
  } = adb;

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

      <AdbFilterBar adb={adb} />
      <ManualDeviceDialog adb={adb} />

      {error && <p className="error-banner mb-4">{error}</p>}
      {success && <p className="success-banner mb-4">{success}</p>}

      <ActivityLog adb={adb} />

      {status && (
        <>
          {unregistered.length > 0 && (
            <div className="my-4 flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
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
                <Button
                  key={d.serial}
                  disabled={busy}
                  onClick={() => onRegisterDevice(d.serial)}
                >
                  {registeringSerial === d.serial ? "Registering…" : `Register ${d.serial}`}
                </Button>
              ))}
            </div>
          )}
          <dl className="my-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-wos-border-subtle/70 bg-wos-panel-raised/40 px-3 py-2 text-xs">
            <div className="flex items-center gap-2">
              <dt className="font-semibold uppercase tracking-wide text-wos-text-muted">adb</dt>
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
          {status.scan_error && <p className="error-banner my-4">Scan: {status.scan_error}</p>}
          <ConfiguredDevicesTable adb={adb} />
          <LiveDevicesTable adb={adb} />
        </>
      )}

      <AppConfirmDialog
        open={deleteDeviceName !== null}
        title="Remove Device"
        confirmLabel={deletingDevice ? "Removing…" : "Remove"}
        variant="danger"
        busy={deletingDevice}
        onClose={() => {
          if (!deletingDevice) setDeleteDeviceName(null);
        }}
        onConfirm={onDeleteDevice}
      >
        <p className="m-0 text-sm text-wos-text-muted">
          Remove <code>{deleteDeviceName}</code> from the fleet registry.
        </p>
      </AppConfirmDialog>
    </>
  );
}
