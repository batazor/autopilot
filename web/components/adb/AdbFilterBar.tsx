import { AppPopover, AppRadioGroup } from "@/components/headless";
import { Button, Icon } from "@/components/ui";
import { REGISTRATION_FILTER_OPTIONS, type RegistrationFilter } from "@/lib/adb/types";
import { ScanPortRangeControls } from "./ScanPortRangeControls";
import type { AdbState } from "./useAdbState";

export function AdbFilterBar({ adb }: { adb: AdbState }) {
  const {
    refreshScanAndRegister,
    scanning,
    busy,
    setManualDeviceOpen,
    filter,
    setFilter,
    registrationFilter,
    setRegistrationFilter,
    portRange,
    updatePortRange,
    status,
  } = adb;

  return (
    <div className="adb-filterbar">
      <Button onClick={refreshScanAndRegister} disabled={scanning}>
        {scanning ? "Scanning…" : "Refresh scan"}
      </Button>
      <button
        type="button"
        className="btn-icon"
        title="Add device manually"
        aria-label="Add device manually"
        onClick={() => setManualDeviceOpen(true)}
        disabled={busy}
      >
        <Icon name="plus" size="sm" />
      </button>
      <label className="module-search adb-filterbar__search">
        <Icon name="search" size="sm" />
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter serial, name, game…"
          type="search"
          aria-label="Filter devices"
        />
        {filter ? (
          <button
            type="button"
            className="btn-icon module-search__clear"
            aria-label="Clear device filter"
            onClick={() => setFilter("")}
          >
            <Icon name="clear" size="sm" />
          </button>
        ) : null}
      </label>
      <AppRadioGroup
        aria-label="Registration filter"
        value={registrationFilter}
        onChange={(v) => setRegistrationFilter(v as RegistrationFilter)}
        options={REGISTRATION_FILTER_OPTIONS}
      />
      <AppPopover
        ariaLabel="Configure TCP scan port range"
        buttonTitle="TCP scan port range — applied on the next scan"
        panelClassName="headless-popover__panel w-72 p-3"
        trigger={
          <>
            Ports {portRange.start}–{portRange.end} · step {portRange.step}
          </>
        }
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
      {status?.scan_port_range && (
        <span className="text-xs text-wos-text-muted">
          scanned {status.scan_port_range.count} port
          {status.scan_port_range.count === 1 ? "" : "s"}
          {status.scan_port_range.start != null &&
            ` (${status.scan_port_range.start}–${status.scan_port_range.end})`}
        </span>
      )}
    </div>
  );
}
