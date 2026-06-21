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
import type { ScrcpyStatus } from "@/lib/config-pages";
import {
  type AdbActivityEntry,
  type CellEntry,
  type RegistrationFilter,
  MANUAL_DEVICE_DEFAULT,
  describeError,
  formatActivityLine,
  matchesQuery,
  scrcpyInstallNote,
} from "@/lib/adb/types";

type AdbStatus = Awaited<ReturnType<typeof fetchAdbStatus>>;

/** All ADB device-management state, scanning, and action handlers. */
export function useAdbState() {
  const [status, setStatus] = useState<AdbStatus | null>(null);
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
  const [registrationFilter, setRegistrationFilter] = useState<RegistrationFilter>("");
  const deferredFilter = useDeferredValue(filter);
  const [savingBackend, setSavingBackend] = useState<string | null>(null);
  // Start from the default so the server and first client render agree (no
  // hydration mismatch); the persisted value is loaded in an effect below.
  const [portRange, setPortRange] = useState(DEFAULT_PORT_RANGE);
  // Read by `load` so editing the inputs doesn't trigger an auto-rescan; the
  // scan only runs on mount or when "Refresh scan" is pressed.
  const portRangeRef = useRef(portRange);
  portRangeRef.current = portRange;

  // Persist + mirror into the ref synchronously so a scan triggered in the same
  // tick (e.g. right after a Reset) sees the new value.
  const updatePortRange = useCallback((next: typeof DEFAULT_PORT_RANGE) => {
    portRangeRef.current = next;
    setPortRange(next);
    saveScanPortRange(next);
  }, []);

  const pushActivity = useCallback((entry: Omit<AdbActivityEntry, "at">) => {
    const now = new Date();
    const at = now.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    setActivity((prev) => [{ at, ...entry }, ...prev].slice(0, 80));
  }, []);

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
    (s: AdbStatus) =>
      s.live_devices.filter(
        (d) =>
          !s.configured.some((c) =>
            adbSerialMatches(c.adb_serial, d.serial, d.canonical_serial),
          ),
      ),
    [],
  );

  const load = useCallback(
    async (opts?: { silent?: boolean }) => {
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
          pushActivity({ tone: "error", label: "ADB scan failed", detail: message });
        }
      } finally {
        setScanning(false);
      }
    },
    [loadProbes, pushActivity],
  );

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
      pushActivity({ tone: "error", label: "Refresh scan failed", detail: message });
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
      pushActivity({ tone: "info", label: "Reset display", detail: serial });
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
      pushActivity({ tone: "info", label: "Register device", detail: serial });
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
      const removedNote = out.removed?.length ? ` Removed ${out.removed.join(", ")}.` : "";
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
      pushActivity({ tone: "info", label: "Use only device", detail: serial });
      const out = await createAdbDevice({ adb_serial: serial, replace_existing: true });
      await reconcileAdbDevices();
      const removedNote = out.removed?.length ? ` Removed ${out.removed.join(", ")}.` : "";
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
      pushActivity({ tone: "info", label: "Remove device", detail: deleteDeviceName });
      const out = await deleteAdbDevice(deleteDeviceName);
      await reconcileAdbDevices();
      setSuccess(`Removed ${out.name}. Its worker stops automatically while the bot is running.`);
      setDeleteDeviceName(null);
      pushActivity({ tone: "success", label: "Device removed", detail: out.name });
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
      pushActivity({ tone: "info", label: "Install scrcpy", detail: serial });
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
      matchesQuery(query, [d.name, d.adb_serial, d.instance_id, d.bluestacks_window_title]),
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

  return {
    // state
    status,
    error,
    success,
    activity,
    setActivity,
    activityCopied,
    resettingSerial,
    scrcpy,
    installingScrcpy,
    registeringSerial,
    manualDeviceOpen,
    setManualDeviceOpen,
    manualDevice,
    setManualDevice,
    creatingManualDevice,
    deleteDeviceName,
    setDeleteDeviceName,
    deletingDevice,
    scanning,
    filter,
    setFilter,
    registrationFilter,
    setRegistrationFilter,
    portRange,
    updatePortRange,
    // derived
    busy,
    unregistered,
    filtersActive,
    configuredFiltered,
    liveFiltered,
    clearFilters,
    sectionCount,
    activityLogText,
    shownActivityLog,
    // actions
    load,
    refreshScanAndRegister,
    onResetDisplay,
    onRegisterDevice,
    onCreateManualDevice,
    onUseOnlyDevice,
    onDeleteDevice,
    onInstallScrcpy,
    onBackendChange,
    copyActivityLog,
  };
}

export type AdbState = ReturnType<typeof useAdbState>;
