"use client";

import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  Dialog,
  DialogBackdrop,
  DialogPanel,
  DialogTitle,
} from "@headlessui/react";
import { PageHeader } from "@/components/PageHeader";
import {
  AppConfirmDialog,
  AppMenu,
  AppPopover,
  AppRadioGroup,
  type AppMenuItem,
} from "@/components/headless";
import { Icon } from "@/components/ui";
import {
  createAdbDevice,
  deleteAdbDevice,
  fetchAdbStatus,
  fetchScrcpyStatus,
  installScrcpy,
  reconcileAdbDevices,
  registerAdbDevice,
  resetAdbDeviceDisplay,
  updateDeviceBackend,
} from "@/lib/api";
import { adbSerialMatches } from "@/lib/adb-serial";
import {
  DEFAULT_PORT_RANGE,
  loadScanPortRange,
  saveScanPortRange,
} from "@/lib/adb-scan-prefs";
import { EmptyState } from "@/components/ui/EmptyState";
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

const MANUAL_DEVICE_DEFAULT = {
  name: "",
  adb_serial: "",
  screenshot_backend: "",
  input_backend: "",
  replace_existing: false,
};

const PORT_INPUT_CLASS =
  "rounded-md border border-wos-border-subtle bg-wos-input px-2 py-1 text-sm text-wos-text focus:border-sky-400/70 focus:outline-none focus:ring-2 focus:ring-sky-400/25";

const REGISTRATION_FILTER_OPTIONS = [
  { value: "", label: "All", title: "Show every device" },
  {
    value: "registered",
    label: "Registered",
    title: "Devices present in the fleet registry",
  },
  {
    value: "unregistered",
    label: "Unregistered",
    title: "Live devices missing from the fleet registry",
  },
];

type RegistrationFilter = "" | "registered" | "unregistered";
type AdbActivityTone = "info" | "success" | "error";
type AdbActivityEntry = {
  at: string;
  tone: AdbActivityTone;
  label: string;
  detail?: string;
};

function matchesQuery(
  query: string,
  fields: Array<string | null | undefined>,
): boolean {
  return fields.some((f) => f?.toLowerCase().includes(query));
}

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

function describeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function activityTag(tone: AdbActivityTone): string {
  if (tone === "success") return "ok";
  if (tone === "error") return "error";
  return "info";
}

function formatActivityLine(entry: AdbActivityEntry): string {
  const detail = entry.detail ? ` — ${entry.detail}` : "";
  return `[${entry.at}] ${activityTag(entry.tone).padEnd(5)} ${entry.label}${detail}`;
}

export default function AdbPage() {
  const [status, setStatus] = useState<Awaited<ReturnType<typeof fetchAdbStatus>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [activity, setActivity] = useState<AdbActivityEntry[]>([]);
  const [activityCopied, setActivityCopied] = useState(false);
  const [resettingSerial, setResettingSerial] = useState<string | null>(null);
  const [scrcpy, setScrcpy] = useState<Record<string, CellEntry<ScrcpyStatus>>>({});
  const [installingScrcpy, setInstallingScrcpy] = useState<string | null>(null);
  const [registeringSerial, setRegisteringSerial] = useState<string | null>(null);
  const [manualDeviceOpen, setManualDeviceOpen] = useState(false);
  const [manualDevice, setManualDevice] = useState(MANUAL_DEVICE_DEFAULT);
  const [creatingManualDevice, setCreatingManualDevice] = useState(false);
  const [deleteDeviceName, setDeleteDeviceName] = useState<string | null>(null);
  const [deletingDevice, setDeletingDevice] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [filter, setFilter] = useState("");
  const [registrationFilter, setRegistrationFilter] =
    useState<RegistrationFilter>("");
  const deferredFilter = useDeferredValue(filter);
  // Start from the default so the server and first client render agree (no
  // hydration mismatch); the persisted value is loaded in an effect below.
  const [portRange, setPortRange] = useState(DEFAULT_PORT_RANGE);
  // Read by `load` so editing the inputs doesn't trigger an auto-rescan; the
  // scan only runs on mount or when "Refresh scan" is pressed.
  const portRangeRef = useRef(portRange);
  portRangeRef.current = portRange;

  // Persist + mirror into the ref synchronously so a scan triggered in the same
  // tick (e.g. right after a Reset) sees the new value.
  const updatePortRange = useCallback(
    (next: typeof DEFAULT_PORT_RANGE) => {
      portRangeRef.current = next;
      setPortRange(next);
      saveScanPortRange(next);
    },
    [],
  );

  const pushActivity = useCallback(
    (entry: Omit<AdbActivityEntry, "at">) => {
      const now = new Date();
      const at = now.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
      setActivity((prev) =>
        [
          {
            at,
            ...entry,
          },
          ...prev,
        ].slice(0, 80),
      );
    },
    [],
  );

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

  const missingLiveDevices = useCallback(
    (s: Awaited<ReturnType<typeof fetchAdbStatus>>) =>
      s.live_devices.filter(
        (d) =>
          !s.configured.some((c) =>
            adbSerialMatches(c.adb_serial, d.serial, d.canonical_serial),
          ),
      ),
    [],
  );

  const load = useCallback(async (opts?: { silent?: boolean }) => {
    setError(null);
    setScanning(true);
    const { start, end, step } = portRangeRef.current;
    const toPort = (v: string) => {
      const n = Number.parseInt(v, 10);
      return Number.isFinite(n) ? n : null;
    };
    if (!opts?.silent) {
      pushActivity({
        tone: "info",
        label: "Scanning ADB devices",
        detail: `ports ${start}-${end}, step ${step}`,
      });
    }
    try {
      const s = await fetchAdbStatus({
        portStart: toPort(start),
        portEnd: toPort(end),
        portStep: toPort(step),
      });
      setStatus(s);
      void loadProbes(s.live_devices.map((d) => d.serial));
      if (!opts?.silent) {
        pushActivity({
          tone: s.scan_error ? "error" : "success",
          label: "ADB scan finished",
          detail: s.scan_error
            ? s.scan_error
            : `${s.live_devices.length} live, ${s.configured.length} configured`,
        });
      }
    } catch (e) {
      const message = describeError(e);
      setError(message);
      if (!opts?.silent) {
        pushActivity({
          tone: "error",
          label: "ADB scan failed",
          detail: message,
        });
      }
    } finally {
      setScanning(false);
    }
  }, [loadProbes, pushActivity]);

  const refreshScanAndRegister = useCallback(async () => {
    setError(null);
    setSuccess(null);
    setScanning(true);
    setRegisteringSerial("__scan__");
    const { start, end, step } = portRangeRef.current;
    const toPort = (v: string) => {
      const n = Number.parseInt(v, 10);
      return Number.isFinite(n) ? n : null;
    };
    try {
      pushActivity({
        tone: "info",
        label: "Refresh scan and register",
        detail: `ports ${start}-${end}, step ${step}`,
      });
      const scanned = await fetchAdbStatus({
        portStart: toPort(start),
        portEnd: toPort(end),
        portStep: toPort(step),
      });
      const missing = missingLiveDevices(scanned);
      if (missing.length > 0) {
        await Promise.all(missing.map((d) => registerAdbDevice(d.serial)));
      }
      await reconcileAdbDevices();
      const refreshed = await fetchAdbStatus({
        portStart: toPort(start),
        portEnd: toPort(end),
        portStep: toPort(step),
      });
      setStatus(refreshed);
      void loadProbes(refreshed.live_devices.map((d) => d.serial));
      pushActivity({
        tone: "success",
        label: "Refresh scan finished",
        detail:
          missing.length > 0
            ? `registered ${missing.length}; ${refreshed.live_devices.length} live`
            : `no missing devices; ${refreshed.live_devices.length} live`,
      });
      if (missing.length > 0) {
        setSuccess(
          `Registered ${missing.length} device${missing.length === 1 ? "" : "s"}.`,
        );
      }
    } catch (e) {
      const message = describeError(e);
      setError(message);
      pushActivity({
        tone: "error",
        label: "Refresh scan failed",
        detail: message,
      });
    } finally {
      setRegisteringSerial(null);
      setScanning(false);
    }
  }, [loadProbes, missingLiveDevices, pushActivity]);

  useEffect(() => {
    // Hydrate the saved range into state + ref before the first scan kicks off
    // (the ref write is synchronous, so the mount `load()` below uses it).
    const saved = loadScanPortRange();
    portRangeRef.current = saved;
    setPortRange(saved);
    load();
    // Mount-only: `load` is stable; we intentionally seed the range once here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onResetDisplay = async (serial: string) => {
    setError(null);
    setSuccess(null);
    setResettingSerial(serial);
    try {
      pushActivity({
        tone: "info",
        label: "Reset display",
        detail: serial,
      });
      const out = await resetAdbDeviceDisplay(serial);
      const parts = [out.wm_size, out.wm_density].filter(Boolean);
      setSuccess(
        parts.length
          ? `Screen reset on ${serial}: ${parts.join(" · ")}`
          : `Screen reset on ${serial}`,
      );
      pushActivity({
        tone: "success",
        label: "Display reset",
        detail: parts.length ? `${serial}: ${parts.join(" · ")}` : serial,
      });
      await load({ silent: true });
    } catch (e) {
      const message = describeError(e);
      setError(message);
      pushActivity({
        tone: "error",
        label: "Display reset failed",
        detail: `${serial}: ${message}`,
      });
    } finally {
      setResettingSerial(null);
    }
  };

  const onRegisterDevice = async (serial: string) => {
    setError(null);
    setSuccess(null);
    setRegisteringSerial(serial);
    try {
      pushActivity({
        tone: "info",
        label: "Register device",
        detail: serial,
      });
      const out = await registerAdbDevice(serial);
      const installNote = scrcpyInstallNote(out.scrcpy_install);
      setSuccess(
        out.created
          ? `Registered ${out.adb_serial} as ${out.name}.${installNote} Its worker starts automatically while the bot is running.`
          : `${out.adb_serial} is already registered as ${out.name}.${installNote}`,
      );
      pushActivity({
        tone: "success",
        label: out.created ? "Device registered" : "Device already registered",
        detail: `${out.adb_serial} as ${out.name}${installNote}`,
      });
      await load({ silent: true });
    } catch (e) {
      const message = describeError(e);
      setError(message);
      pushActivity({
        tone: "error",
        label: "Register device failed",
        detail: `${serial}: ${message}`,
      });
    } finally {
      setRegisteringSerial(null);
    }
  };

  const onCreateManualDevice = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    setCreatingManualDevice(true);
    try {
      pushActivity({
        tone: "info",
        label: "Add manual device",
        detail: manualDevice.adb_serial.trim(),
      });
      const out = await createAdbDevice({
        name: manualDevice.name.trim(),
        adb_serial: manualDevice.adb_serial.trim(),
        screenshot_backend: manualDevice.screenshot_backend,
        input_backend: manualDevice.input_backend,
        replace_existing: manualDevice.replace_existing,
      });
      await reconcileAdbDevices();
      const installNote = scrcpyInstallNote(out.scrcpy_install);
      const removedNote = out.removed?.length
        ? ` Removed ${out.removed.join(", ")}.`
        : "";
      setSuccess(
        out.created
          ? `Added ${out.adb_serial} as ${out.name}.${removedNote}${installNote} Its worker starts automatically while the bot is running.`
          : `${out.adb_serial} is already registered as ${out.name}.${removedNote}${installNote}`,
      );
      setManualDevice(MANUAL_DEVICE_DEFAULT);
      setManualDeviceOpen(false);
      pushActivity({
        tone: "success",
        label: out.created ? "Manual device added" : "Manual device already exists",
        detail: `${out.adb_serial} as ${out.name}${removedNote}${installNote}`,
      });
      await load({ silent: true });
    } catch (err) {
      const message = describeError(err);
      setError(message);
      pushActivity({
        tone: "error",
        label: "Add manual device failed",
        detail: `${manualDevice.adb_serial.trim()}: ${message}`,
      });
    } finally {
      setCreatingManualDevice(false);
    }
  };

  const onUseOnlyDevice = async (serial: string) => {
    setError(null);
    setSuccess(null);
    setRegisteringSerial(serial);
    try {
      pushActivity({
        tone: "info",
        label: "Use only device",
        detail: serial,
      });
      const out = await createAdbDevice({
        adb_serial: serial,
        replace_existing: true,
      });
      await reconcileAdbDevices();
      const removedNote = out.removed?.length
        ? ` Removed ${out.removed.join(", ")}.`
        : "";
      const installNote = scrcpyInstallNote(out.scrcpy_install);
      setSuccess(
        out.created
          ? `Using ${out.adb_serial} as ${out.name}.${removedNote}${installNote}`
          : `${out.adb_serial} is registered as ${out.name}.${removedNote}${installNote}`,
      );
      pushActivity({
        tone: "success",
        label: "Device set as only configured device",
        detail: `${out.adb_serial} as ${out.name}${removedNote}${installNote}`,
      });
      await load({ silent: true });
    } catch (err) {
      const message = describeError(err);
      setError(message);
      pushActivity({
        tone: "error",
        label: "Use only device failed",
        detail: `${serial}: ${message}`,
      });
    } finally {
      setRegisteringSerial(null);
    }
  };

  const onDeleteDevice = async () => {
    if (!deleteDeviceName) return;
    setError(null);
    setSuccess(null);
    setDeletingDevice(true);
    try {
      pushActivity({
        tone: "info",
        label: "Remove device",
        detail: deleteDeviceName,
      });
      const out = await deleteAdbDevice(deleteDeviceName);
      await reconcileAdbDevices();
      setSuccess(`Removed ${out.name}. Its worker stops automatically while the bot is running.`);
      setDeleteDeviceName(null);
      pushActivity({
        tone: "success",
        label: "Device removed",
        detail: out.name,
      });
      await load({ silent: true });
    } catch (err) {
      const message = describeError(err);
      setError(message);
      pushActivity({
        tone: "error",
        label: "Remove device failed",
        detail: `${deleteDeviceName}: ${message}`,
      });
    } finally {
      setDeletingDevice(false);
    }
  };

  const onInstallScrcpy = async (serial: string) => {
    setError(null);
    setSuccess(null);
    setInstallingScrcpy(serial);
    try {
      pushActivity({
        tone: "info",
        label: "Install scrcpy",
        detail: serial,
      });
      const out = await installScrcpy(serial);
      if (out.installed) {
        setSuccess(`Scrcpy server installed on ${serial} (${out.abi})`);
        pushActivity({
          tone: "success",
          label: "Scrcpy installed",
          detail: `${serial}: ${out.abi ?? "abi unknown"}`,
        });
      } else {
        setError(`Scrcpy install on ${serial} failed: ${out.last_error ?? "unknown error"}`);
        pushActivity({
          tone: "error",
          label: "Scrcpy install failed",
          detail: `${serial}: ${out.last_error ?? "unknown error"}`,
        });
      }
      setScrcpy((prev) => ({ ...prev, [serial]: out }));
    } catch (e) {
      const message = describeError(e);
      setError(message);
      pushActivity({
        tone: "error",
        label: "Scrcpy install failed",
        detail: `${serial}: ${message}`,
      });
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
      pushActivity({
        tone: "info",
        label: "Update backend",
        detail: `${serial}: ${field} -> ${value || "auto"}`,
      });
      const out = await updateDeviceBackend(serial, { [field]: value });
      const label = field === "screenshot_backend" ? "screen capture" : "input";
      const shown = value || "auto";
      const installNote = scrcpyInstallNote(out.scrcpy_install);
      setSuccess(
        out.restart_required
          ? `${label} backend on ${serial} → ${shown}.${installNote} Restart the bot to apply.`
          : `${label} backend on ${serial} → ${shown}.${installNote}`,
      );
      pushActivity({
        tone: "success",
        label: "Backend updated",
        detail: `${serial}: ${label} -> ${shown}${installNote}`,
      });
      await load({ silent: true });
    } catch (e) {
      const message = describeError(e);
      setError(message);
      pushActivity({
        tone: "error",
        label: "Backend update failed",
        detail: `${serial}: ${message}`,
      });
    } finally {
      setSavingBackend(null);
    }
  };

  const busy =
    installingScrcpy !== null ||
    registeringSerial !== null ||
    creatingManualDevice ||
    deletingDevice ||
    resettingSerial !== null ||
    savingBackend !== null;

  const unregistered = status ? missingLiveDevices(status) : [];

  const query = deferredFilter.trim().toLowerCase();
  const filtersActive = query !== "" || registrationFilter !== "";

  const configuredFiltered = useMemo(() => {
    if (!status) return [];
    // Configured rows are registered by definition, so the "unregistered"
    // facet empties this table rather than silently ignoring the filter.
    if (registrationFilter === "unregistered") return [];
    if (!query) return status.configured;
    return status.configured.filter((d) =>
      matchesQuery(query, [
        d.name,
        d.adb_serial,
        d.instance_id,
        d.bluestacks_window_title,
      ]),
    );
  }, [status, query, registrationFilter]);

  const liveFiltered = useMemo(() => {
    if (!status) return [];
    return status.live_devices.filter((d) => {
      const registered = status.configured.some((c) =>
        adbSerialMatches(c.adb_serial, d.serial, d.canonical_serial),
      );
      if (registrationFilter === "registered" && !registered) return false;
      if (registrationFilter === "unregistered" && registered) return false;
      if (!query) return true;
      const games = (d.detected_games ?? []).flatMap((g) => [g.id, g.label]);
      return matchesQuery(query, [d.serial, d.canonical_serial, d.line, ...games]);
    });
  }, [status, query, registrationFilter]);

  const clearFilters = () => {
    setFilter("");
    setRegistrationFilter("");
  };

  const renderNoMatchesRow = (colSpan: number) => (
    <tr>
      <td colSpan={colSpan}>
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
  );

  const sectionCount = (shown: number, total: number) =>
    filtersActive ? `${shown}/${total}` : `${total}`;

  const activityLogText = useMemo(
    () => activity.map(formatActivityLine).join("\n"),
    [activity],
  );
  const shownActivityLog = activityLogText || "No ADB activity yet.";

  const copyActivityLog = async () => {
    if (!activityLogText) return;
    await navigator.clipboard?.writeText(activityLogText);
    setActivityCopied(true);
    window.setTimeout(() => setActivityCopied(false), 1200);
  };

  const renderPortRangeControls = (close: () => void) => (
    <div className="flex flex-col gap-3 text-xs text-wos-text-muted">
      <span className="font-semibold uppercase tracking-wide">
        TCP scan port range
      </span>
      <div className="flex items-end gap-2">
        <label className="flex flex-col gap-1">
          <span>From</span>
          <input
            type="number"
            min={1}
            max={65535}
            className={`${PORT_INPUT_CLASS} w-20`}
            value={portRange.start}
            onChange={(e) =>
              updatePortRange({ ...portRangeRef.current, start: e.target.value })
            }
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
            onChange={(e) =>
              updatePortRange({ ...portRangeRef.current, end: e.target.value })
            }
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
            onChange={(e) =>
              updatePortRange({ ...portRangeRef.current, step: e.target.value })
            }
          />
        </label>
      </div>
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          className="btn-secondary"
          onClick={() => updatePortRange(DEFAULT_PORT_RANGE)}
          disabled={
            portRange.start === DEFAULT_PORT_RANGE.start &&
            portRange.end === DEFAULT_PORT_RANGE.end &&
            portRange.step === DEFAULT_PORT_RANGE.step
          }
        >
          Reset
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={scanning}
          onClick={() => {
            close();
            void refreshScanAndRegister();
          }}
        >
          Rescan now
        </button>
      </div>
    </div>
  );

  const renderNoLiveDevicesRow = () => (
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
                {({ close }) => renderPortRangeControls(close)}
              </AppPopover>
              <button
                type="button"
                className="btn-primary"
                onClick={() => setManualDeviceOpen(true)}
                disabled={busy}
              >
                <Icon name="plus" size="sm" />
                Add manually
              </button>
            </div>
          }
        />
      </td>
    </tr>
  );

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
      <div className="adb-filterbar">
        <button
          type="button"
          className="btn-secondary"
          onClick={refreshScanAndRegister}
          disabled={scanning}
        >
          {scanning ? "Scanning…" : "Refresh scan"}
        </button>
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
            renderPortRangeControls(close)
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
            <DialogTitle className="headless-dialog__title">
              Add ADB Device
            </DialogTitle>
            <form onSubmit={onCreateManualDevice}>
              <div className="headless-dialog__body">
                <div className="adb-manual-dialog__fields">
                  <label className="adb-manual-dialog__field">
                    <span>Name</span>
                    <input
                      className="adb-manual-dialog__input"
                      value={manualDevice.name}
                      onChange={(e) =>
                        setManualDevice((prev) => ({
                          ...prev,
                          name: e.target.value,
                        }))
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
                        setManualDevice((prev) => ({
                          ...prev,
                          adb_serial: e.target.value,
                        }))
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
                        setManualDevice((prev) => ({
                          ...prev,
                          input_backend: e.target.value,
                        }))
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
                    <span>
                      Replace existing configured devices
                    </span>
                  </label>
                </div>
              </div>
              <div className="headless-dialog__actions">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setManualDeviceOpen(false)}
                  disabled={creatingManualDevice}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={creatingManualDevice || !manualDevice.adb_serial.trim()}
                >
                  {creatingManualDevice ? "Adding…" : "Add device"}
                </button>
              </div>
            </form>
          </DialogPanel>
        </div>
      </Dialog>
      {error && <p className="error-banner mb-4">{error}</p>}
      {success && <p className="success-banner mb-4">{success}</p>}
      <section className="panel panel--spaced">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="m-0 text-base font-semibold text-wos-text">
            Activity log
          </h2>
          <span className="text-xs text-wos-text-muted">
            {activity.length ? `${activity.length} event${activity.length === 1 ? "" : "s"}` : "waiting"}
          </span>
          <button
            type="button"
            className="btn-secondary ml-auto inline-flex items-center gap-1 px-2 py-1 text-xs"
            disabled={!activityLogText}
            onClick={copyActivityLog}
          >
            <Icon name="copy" size="sm" />
            {activityCopied ? "Copied" : "Copy logs"}
          </button>
          <button
            type="button"
            className="btn-secondary inline-flex items-center gap-1 px-2 py-1 text-xs"
            disabled={!activity.length}
            onClick={() => setActivity([])}
          >
            <Icon name="trash" size="sm" />
            Clear
          </button>
        </div>
        <pre className="mt-3 max-h-56 overflow-auto rounded-md bg-wos-surface p-2 font-mono text-xs leading-relaxed text-wos-text-secondary">{shownActivityLog}</pre>
      </section>
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
            <h2>
              Configured ({sectionCount(configuredFiltered.length, status.configured.length)})
            </h2>
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
                  {configuredFiltered.length === 0 &&
                    status.configured.length > 0 &&
                    renderNoMatchesRow(7)}
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
          <section className="panel panel--spaced">
            <h2>
              Live devices ({sectionCount(liveFiltered.length, status.live_devices.length)})
            </h2>
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
                  {liveFiltered.length === 0 &&
                    status.live_devices.length > 0 &&
                    renderNoMatchesRow(5)}
                  {status.live_devices.length === 0 && renderNoLiveDevicesRow()}
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
                        label:
                          registeringSerial === d.serial
                            ? "Updating..."
                            : "Use only this device",
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
