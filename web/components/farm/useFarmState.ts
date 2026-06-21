"use client";

import { useCallback, useEffect, useState } from "react";
import type { PillTone } from "@/components/ui";
import {
  type CharacterEdit,
  type FarmAccount,
  type Pending,
  type RegistrationStatus,
  type RoleOption,
  type StartRegistrationOptions,
  type StartRegistrationResponse,
  STATUSES,
} from "@/lib/farm/types";

const EMPTY_EDIT: CharacterEdit = { server: "", fid: "", nickname: "" };

/**
 * All farm-dashboard state, polling, and action handlers. Kept out of the view
 * so {@link FarmDashboard} and its section components stay presentational.
 */
export function useFarmState() {
  const [pending, setPending] = useState<Pending>(null);
  const [accounts, setAccounts] = useState<FarmAccount[]>([]);
  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [registrationRunning, setRegistrationRunning] = useState(false);
  const [registrationStatus, setRegistrationStatus] =
    useState<RegistrationStatus | null>(null);

  // generate form
  const [count, setCount] = useState(1);
  const [seed, setSeed] = useState("");
  // table state
  const [filter, setFilter] = useState<string>("all");
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [characterEdits, setCharacterEdits] = useState<
    Record<string, CharacterEdit>
  >({});
  const [bindEdits, setBindEdits] = useState<Record<string, string>>({});
  const [deleteTarget, setDeleteTarget] = useState<FarmAccount | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [logsCopied, setLogsCopied] = useState(false);
  // Character sub-tables are collapsed by default; track which rows are open.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggleExpanded = useCallback((username: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(username)) next.delete(username);
      else next.add(username);
      return next;
    });
  }, []);

  const refreshAccounts = useCallback(() => {
    fetch("/api/farm/accounts")
      .then((r) => r.json())
      .then((d) => setAccounts(d.accounts ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      fetch("/api/farm/registration/status")
        .then((r) => r.json())
        .then((d) => {
          const status = d as RegistrationStatus;
          if (!cancelled) setPending(d.pending ?? null);
          if (!cancelled) setRegistrationStatus(status);
          if (!cancelled) setRegistrationRunning(Boolean(status.running));
        })
        .catch(() => {});
    };
    poll();
    refreshAccounts();
    fetch("/api/farm/roles")
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) setRoles(d.roles ?? []);
      })
      .catch(() => {});
    const t = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [refreshAccounts]);

  const post = async (url: string, body?: unknown) => {
    setError(null);
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  };

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
    } finally {
      setBusy(false);
    }
  };

  const generate = () =>
    guard(async () => {
      await post("/api/farm/generate", {
        count,
        seed: seed.trim() || null,
      });
      setSeed("");
      refreshAccounts();
    });

  const startRegistration = (opts: StartRegistrationOptions = {}) =>
    guard(async () => {
      const d = (await post("/api/farm/registration/start", {
        username: opts.username ?? null,
        existing: opts.existing ?? false,
        seed: seed.trim() || null,
      })) as StartRegistrationResponse;
      setRegistrationRunning(Boolean(d.running));
      setRegistrationStatus((prev) => ({
        running: Boolean(d.running),
        pending: d.pending ?? null,
        pid: d.pid ?? null,
        started_at: d.started_at ?? null,
        finished_at: null,
        exit_code: null,
        log_path: d.log_path ?? prev?.log_path ?? null,
        log_tail: prev?.log_tail ?? "",
      }));
      if (d.pending) setPending(d.pending);
      setTimeout(refreshAccounts, 800);
    });

  const sendVerdict = (outcome: "failed") =>
    guard(async () => {
      if (!pending) return;
      await post("/api/farm/registration/done", {
        username: pending.username,
        outcome,
      });
      setPending(null);
      setRegistrationRunning(false);
      setTimeout(refreshAccounts, 800);
    });

  const reveal = (username: string) =>
    guard(async () => {
      const d = await fetch(`/api/farm/accounts/${username}/secret`).then((r) =>
        r.json(),
      );
      setSecrets((s) => ({ ...s, [username]: d.password }));
    });

  const updateCharacterEdit = (
    username: string,
    patch: Partial<CharacterEdit>,
  ) => {
    setCharacterEdits((m) => ({
      ...m,
      [username]: { ...(m[username] ?? EMPTY_EDIT), ...patch },
    }));
  };

  const saveCharacter = (username: string) =>
    guard(async () => {
      const edit = characterEdits[username] ?? EMPTY_EDIT;
      await post(`/api/farm/accounts/${username}/characters`, {
        server: edit.server.trim(),
        fid: edit.fid.trim(),
        nickname: edit.nickname.trim(),
      });
      setCharacterEdits((m) => ({ ...m, [username]: { ...EMPTY_EDIT } }));
      refreshAccounts();
    });

  const removeCharacter = (username: string, server: string) =>
    guard(async () => {
      const res = await fetch(
        `/api/farm/accounts/${username}/characters/${encodeURIComponent(server)}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      refreshAccounts();
    });

  const bind = (username: string) =>
    guard(async () => {
      await post(`/api/farm/accounts/${username}/bind`, {
        device_serial: bindEdits[username] ?? "",
      });
      refreshAccounts();
    });

  const setCharacterRole = (username: string, fid: string, role: string) =>
    guard(async () => {
      // Optimistic: reflect the pick immediately (the 2s poll would otherwise lag).
      setAccounts((prev) =>
        prev.map((a) =>
          a.username === username
            ? {
                ...a,
                characters: a.characters.map((c) =>
                  c.fid === fid ? { ...c, role } : c,
                ),
              }
            : a,
        ),
      );
      await post(
        `/api/farm/accounts/${username}/characters/${encodeURIComponent(fid)}/role`,
        { role },
      );
      refreshAccounts();
    });

  const remove = (account: FarmAccount) => {
    setDeleteTarget(account);
    setDeleteConfirm("");
  };

  const confirmRemove = () =>
    guard(async () => {
      if (!deleteTarget) return;
      const res = await fetch(`/api/farm/accounts/${deleteTarget.username}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm_username: deleteConfirm }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDeleteTarget(null);
      setDeleteConfirm("");
      refreshAccounts();
    });

  const copyRegistrationLogs = async () => {
    const header = [
      `pid: ${registrationStatus?.pid ?? "—"}`,
      `exit: ${registrationStatus?.exit_code ?? "—"}`,
      `log: ${registrationStatus?.log_path ?? "—"}`,
    ].join("\n");
    const text = `${header}\n\n${registrationStatus?.log_tail ?? ""}`.trim();
    await navigator.clipboard?.writeText(text);
    setLogsCopied(true);
    window.setTimeout(() => setLogsCopied(false), 1200);
  };

  const clearRegistrationLogs = () =>
    guard(async () => {
      const res = await fetch("/api/farm/registration/log", {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setRegistrationStatus(null);
      setLogsCopied(false);
    });

  const counts = STATUSES.map((s) => ({
    status: s,
    n: accounts.filter((a) => a.status === s).length,
  }));
  const shown =
    filter === "all" ? accounts : accounts.filter((a) => a.status === filter);
  const allExpanded =
    shown.length > 0 && shown.every((a) => expanded.has(a.username));
  const toggleAll = () =>
    setExpanded(allExpanded ? new Set() : new Set(shown.map((a) => a.username)));
  const registrationFailed =
    !pending &&
    registrationStatus?.exit_code !== null &&
    registrationStatus?.exit_code !== undefined &&
    registrationStatus.exit_code !== 0;
  const registrationLabel = pending
    ? `pending: ${pending.username}`
    : registrationRunning
      ? `starting${registrationStatus?.pid ? ` pid ${registrationStatus.pid}` : ""}`
      : registrationFailed
        ? `failed ${registrationStatus?.exit_code}`
        : "idle";
  const registrationTone: PillTone = pending
    ? "pending"
    : registrationRunning
      ? "busy"
      : registrationFailed
        ? "danger"
        : "ok";
  const registrationPulse = Boolean(pending);
  const canStartRegistration = !busy && !pending && !registrationRunning;

  return {
    // data
    pending,
    accounts,
    roles,
    error,
    busy,
    registrationRunning,
    registrationStatus,
    secrets,
    bindEdits,
    setBindEdits,
    characterEdits,
    expanded,
    // generate form
    count,
    setCount,
    seed,
    setSeed,
    // filter
    filter,
    setFilter,
    counts,
    shown,
    // delete dialog
    deleteTarget,
    setDeleteTarget,
    deleteConfirm,
    setDeleteConfirm,
    // logs
    logsCopied,
    // derived
    allExpanded,
    registrationLabel,
    registrationTone,
    registrationPulse,
    canStartRegistration,
    // actions
    toggleExpanded,
    toggleAll,
    generate,
    startRegistration,
    sendVerdict,
    reveal,
    updateCharacterEdit,
    saveCharacter,
    removeCharacter,
    bind,
    setCharacterRole,
    remove,
    confirmRemove,
    copyRegistrationLogs,
    clearRegistrationLogs,
  };
}

export type FarmState = ReturnType<typeof useFarmState>;
