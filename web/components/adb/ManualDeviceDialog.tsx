import {
  Dialog,
  DialogBackdrop,
  DialogPanel,
  DialogTitle,
} from "@headlessui/react";
import { Button } from "@/components/ui";
import { INPUT_BACKEND_OPTIONS } from "@/lib/adb/types";
import type { AdbState } from "./useAdbState";

export function ManualDeviceDialog({ adb }: { adb: AdbState }) {
  const {
    manualDeviceOpen,
    setManualDeviceOpen,
    manualDevice,
    setManualDevice,
    creatingManualDevice,
    onCreateManualDevice,
  } = adb;

  return (
    <Dialog
      open={manualDeviceOpen}
      onClose={() => {
        if (!creatingManualDevice) setManualDeviceOpen(false);
      }}
      className="headless-dialog-root"
    >
      <DialogBackdrop transition className="headless-dialog__backdrop" />
      <div className="headless-dialog__container">
        <DialogPanel transition className="headless-dialog__panel adb-manual-dialog">
          <DialogTitle className="headless-dialog__title">Add ADB Device</DialogTitle>
          <form onSubmit={onCreateManualDevice}>
            <div className="headless-dialog__body">
              <div className="adb-manual-dialog__fields">
                <label className="adb-manual-dialog__field">
                  <span>Name</span>
                  <input
                    className="adb-manual-dialog__input"
                    value={manualDevice.name}
                    onChange={(e) =>
                      setManualDevice((prev) => ({ ...prev, name: e.target.value }))
                    }
                    placeholder="bs1"
                    disabled={creatingManualDevice}
                  />
                </label>
                <label className="adb-manual-dialog__field">
                  <span>ADB serial</span>
                  <input
                    className="adb-manual-dialog__input"
                    value={manualDevice.adb_serial}
                    onChange={(e) =>
                      setManualDevice((prev) => ({ ...prev, adb_serial: e.target.value }))
                    }
                    placeholder="127.0.0.1:5615"
                    required
                    disabled={creatingManualDevice}
                  />
                </label>
                <label className="adb-manual-dialog__field">
                  <span>Capture</span>
                  <select
                    className="adb-manual-dialog__input"
                    value={manualDevice.screenshot_backend}
                    onChange={(e) =>
                      setManualDevice((prev) => ({
                        ...prev,
                        screenshot_backend: e.target.value,
                      }))
                    }
                    disabled={creatingManualDevice}
                  >
                    <option value="">auto (scrcpy)</option>
                    <option value="adb">adb</option>
                    <option value="scrcpy">scrcpy</option>
                  </select>
                </label>
                <label className="adb-manual-dialog__field">
                  <span>Input</span>
                  <select
                    className="adb-manual-dialog__input"
                    value={manualDevice.input_backend}
                    onChange={(e) =>
                      setManualDevice((prev) => ({ ...prev, input_backend: e.target.value }))
                    }
                    disabled={creatingManualDevice}
                  >
                    {INPUT_BACKEND_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="adb-manual-dialog__switch">
                  <input
                    type="checkbox"
                    checked={manualDevice.replace_existing}
                    onChange={(e) =>
                      setManualDevice((prev) => ({
                        ...prev,
                        replace_existing: e.target.checked,
                      }))
                    }
                    disabled={creatingManualDevice}
                  />
                  <span>Replace existing configured devices</span>
                </label>
              </div>
            </div>
            <div className="headless-dialog__actions">
              <Button
                onClick={() => setManualDeviceOpen(false)}
                disabled={creatingManualDevice}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                variant="primary"
                disabled={creatingManualDevice || !manualDevice.adb_serial.trim()}
              >
                {creatingManualDevice ? "Adding…" : "Add device"}
              </Button>
            </div>
          </form>
        </DialogPanel>
      </div>
    </Dialog>
  );
}
