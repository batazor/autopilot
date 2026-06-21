import { Button } from "@/components/ui";
import { DEFAULT_PORT_RANGE } from "@/lib/adb-scan-prefs";
import { PORT_INPUT_CLASS } from "@/lib/adb/types";

type PortRange = typeof DEFAULT_PORT_RANGE;

export function ScanPortRangeControls({
  portRange,
  updatePortRange,
  scanning,
  onRescan,
  onClose,
}: {
  portRange: PortRange;
  updatePortRange: (next: PortRange) => void;
  scanning: boolean;
  onRescan: () => void;
  onClose: () => void;
}) {
  return (
    <div className="flex flex-col gap-3 text-xs text-wos-text-muted">
      <span className="font-semibold uppercase tracking-wide">TCP scan port range</span>
      <div className="flex items-end gap-2">
        <label className="flex flex-col gap-1">
          <span>From</span>
          <input
            type="number"
            min={1}
            max={65535}
            className={`${PORT_INPUT_CLASS} w-20`}
            value={portRange.start}
            onChange={(e) => updatePortRange({ ...portRange, start: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span>To</span>
          <input
            type="number"
            min={1}
            max={65535}
            className={`${PORT_INPUT_CLASS} w-20`}
            value={portRange.end}
            onChange={(e) => updatePortRange({ ...portRange, end: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span>Step</span>
          <input
            type="number"
            min={1}
            max={65535}
            className={`${PORT_INPUT_CLASS} w-16`}
            value={portRange.step}
            onChange={(e) => updatePortRange({ ...portRange, step: e.target.value })}
          />
        </label>
      </div>
      <div className="flex items-center justify-between gap-2">
        <Button
          onClick={() => updatePortRange(DEFAULT_PORT_RANGE)}
          disabled={
            portRange.start === DEFAULT_PORT_RANGE.start &&
            portRange.end === DEFAULT_PORT_RANGE.end &&
            portRange.step === DEFAULT_PORT_RANGE.step
          }
        >
          Reset
        </Button>
        <Button
          disabled={scanning}
          onClick={() => {
            onClose();
            onRescan();
          }}
        >
          Rescan now
        </Button>
      </div>
    </div>
  );
}
