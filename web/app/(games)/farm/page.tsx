"use client";

import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import {
  Dialog,
  DialogBackdrop,
  DialogPanel,
  DialogTitle,
} from "@headlessui/react";
import Link from "next/link";
import { Fragment, useCallback, useEffect, useState } from "react";
import { ErrorBanner } from "@/components/feedback";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { Icon } from "@/components/ui/Icon";
import { fetchLicenseStatus } from "@/lib/api";

type Pending = {
  username: string;
  started_at?: string;
  stage?: string;
  image_code?: string;
  slider?: string;
  slider_expected?: string;
} | null;
type StartRegistrationResponse = {
  running?: boolean;
  pending?: Pending;
  pid?: number | null;
  started_at?: number | null;
  log_path?: string | null;
};
type StartRegistrationOptions = {
  username?: string;
  existing?: boolean;
};
type RegistrationStatus = {
  running: boolean;
  pending: Pending;
  pid: number | null;
  started_at: number | null;
  finished_at: number | null;
  exit_code: number | null;
  log_path: string | null;
  log_tail: string;
};
type ActiveInGame = {
  fid: string;
  instances: {
    instance_id: string;
    screen: string;
    task: string;
  }[];
};
type FarmCharacter = {
  server: string;
  fid: string;
  nickname: string;
  created_at: number | null;
  updated_at: number | null;
  note: string;
  active: ActiveInGame | null;
};
type FarmAccount = {
  username: string;
  status: string;
  server: string;
  device_serial: string | null;
  registered_at: number | null;
  active: ActiveInGame | null;
  characters: FarmCharacter[];
};
type CharacterEdit = {
  server: string;
  fid: string;
  nickname: string;
};

const STATUSES = ["pending", "registered", "bound", "failed"] as const;

function activeTitle(active?: ActiveInGame | null) {
  if (!active) return undefined;
  const instances = active.instances
    .map((i) =>
      [i.instance_id, i.screen || null, i.task || null]
        .filter(Boolean)
        .join(" · "),
    )
    .join("; ");
  return instances ? `In game: ${instances}` : "In game";
}

function R5Gate() {
  return (
    <div className="page-stack">
      <PageHeader title="Farm" />
      <section className="panel">
        <div className="flex items-start gap-4">
          <span
            className="rounded-full border border-amber-400/40 bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-300"
            aria-hidden
          >
            R5
          </span>
          <div className="min-w-0">
            <h2 className="m-0 text-base font-semibold text-wos-text">
              Farm is an owner-only (R5) feature
            </h2>
            <p className="muted mt-1">
              Generating beta farm accounts and confirming their registration is
              gated behind the R5 owner tier and is still in development.
            </p>
            <div className="mt-3">
              <Link href="/license" className="btn-primary">
                Open License
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

export default function FarmPage() {
  const [tier, setTier] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    const pull = () => {
      fetchLicenseStatus()
        .then((st) => {
          if (!cancelled) setTier(st.active && st.tier ? st.tier : null);
        })
        .catch(() => {
          if (!cancelled) setTier(null);
        });
    };
    pull();
    window.addEventListener("wos:license:updated", pull);
    return () => {
      cancelled = true;
      window.removeEventListener("wos:license:updated", pull);
    };
  }, []);
  if (tier === undefined) return <PageLoading />;
  if (tier !== "r5") return <R5Gate />;
  return <FarmInner />;
}

function FarmInner() {
  const [pending, setPending] = useState<Pending>(null);
  const [accounts, setAccounts] = useState<FarmAccount[]>([]);
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

  const sendVerdict = (outcome: "done" | "failed") =>
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
    const empty: CharacterEdit = { server: "", fid: "", nickname: "" };
    setCharacterEdits((m) => ({
      ...m,
      [username]: { ...(m[username] ?? empty), ...patch },
    }));
  };

  const saveCharacter = (username: string) =>
    guard(async () => {
      const edit = characterEdits[username] ?? {
        server: "",
        fid: "",
        nickname: "",
      };
      await post(`/api/farm/accounts/${username}/characters`, {
        server: edit.server.trim(),
        fid: edit.fid.trim(),
        nickname: edit.nickname.trim(),
      });
      setCharacterEdits((m) => ({
        ...m,
        [username]: { server: "", fid: "", nickname: "" },
      }));
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
  const registrationTone = pending
    ? "status-pending pulse"
    : registrationRunning
      ? "pill-busy"
      : registrationFailed
        ? "pill-danger"
        : "status-idle";
  const canStartRegistration = !busy && !pending && !registrationRunning;
  const accountColumns: ColumnDef<FarmAccount>[] = [
    {
      id: "expander",
      header: () => null,
      cell: ({ row }) => {
        const a = row.original;
        const open = expanded.has(a.username);
        return (
          <button
            type="button"
            className="farm-expander"
            aria-expanded={open}
            aria-label={`${open ? "Hide" : "Show"} characters for ${a.username}`}
            onClick={() => toggleExpanded(a.username)}
          >
            <Icon
              name="chevron-right"
              size="sm"
              className={`farm-expander__icon${open ? " farm-expander__icon--open" : ""}`}
            />
          </button>
        );
      },
    },
    {
      accessorKey: "username",
      header: "Username",
      cell: ({ row }) => {
        const a = row.original;
        return (
          <button
            type="button"
            className="farm-username"
            onClick={() => toggleExpanded(a.username)}
            title={activeTitle(a.active) ?? "Toggle characters"}
          >
            <span className="flex items-center gap-1.5 font-semibold text-wos-text">
              {a.active ? <ActiveMarker active={a.active} /> : null}
              <span>{a.username}</span>
            </span>
            <span className="text-xs text-wos-text-muted">{a.server}</span>
          </button>
        );
      },
    },
    {
      id: "password",
      header: "Password",
      cell: ({ row }) => {
        const a = row.original;
        return secrets[a.username] ? (
          <span className="flex items-center gap-2">
            <code className="rounded bg-wos-panel-raised px-2 py-1 text-xs text-sky-100">
              {secrets[a.username]}
            </code>
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-1 px-2 py-1 text-xs"
              onClick={() => navigator.clipboard?.writeText(secrets[a.username])}
            >
              <Icon name="copy" size="sm" />
              Copy
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="btn-secondary px-2 py-1 text-xs"
            disabled={busy}
            onClick={() => reveal(a.username)}
          >
            Reveal
          </button>
        );
      },
    },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
    },
    {
      id: "characters",
      header: "Characters",
      cell: ({ row }) => {
        const a = row.original;
        const n = a.characters.length;
        const open = expanded.has(a.username);
        const hasActive = a.characters.some((c) => c.active);
        return (
          <button
            type="button"
            className={`farm-char-count${hasActive ? " farm-char-count--active" : ""}`}
            aria-expanded={open}
            onClick={() => toggleExpanded(a.username)}
            title={`${open ? "Hide" : "Show"} ${n} character${n === 1 ? "" : "s"}`}
          >
            <strong>{n}</strong>
            <span>{n === 1 ? "character" : "characters"}</span>
          </button>
        );
      },
    },
    {
      id: "device",
      header: "Device",
      cell: ({ row }) => {
        const a = row.original;
        return a.device_serial ? (
          <span className="text-wos-text">{a.device_serial}</span>
        ) : (
          <span className="flex items-center gap-2">
            <input
              type="text"
              placeholder="serial"
              onChange={(e) =>
                setBindEdits((m) => ({ ...m, [a.username]: e.target.value }))
              }
              className="w-40 rounded-md border border-wos-border-subtle bg-wos-input px-2 py-1"
            />
            <button
              type="button"
              className="btn-secondary px-2 py-1 text-xs"
              disabled={busy}
              onClick={() => bind(a.username)}
            >
              Bind
            </button>
          </span>
        );
      },
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => {
        const a = row.original;
        return (
          <div className="flex justify-end gap-1.5">
            {a.status === "pending" || a.status === "failed" ? (
              <button
                type="button"
                className="btn-primary inline-flex items-center gap-1 px-2 py-1 text-xs"
                disabled={!canStartRegistration}
                onClick={() =>
                  startRegistration({
                    username: a.username,
                    existing: true,
                  })
                }
                title={`Register ${a.username}`}
              >
                <Icon name="play" size="sm" />
                Register
              </button>
            ) : null}
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-1 px-2 py-1 text-xs text-red-200"
              disabled={busy}
              onClick={() => remove(a)}
            >
              <Icon name="trash" size="sm" />
              Delete
            </button>
          </div>
        );
      },
    },
  ];
  const accountsTable = useReactTable({
    data: shown,
    columns: accountColumns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.username,
  });

  return (
    <>
      <div className="page-stack">
      <PageHeader
        title="Farm"
        actions={
          <>
            <span
              className={`status-pill status-pill--lg ${registrationTone}`}
            >
              <span className="status-pill__dot" />
              {registrationLabel}
            </span>
            <button
              type="button"
              className="btn-primary inline-flex items-center gap-1.5"
              disabled={!canStartRegistration}
              onClick={() => startRegistration()}
              title={pending ? "Registration is already waiting for confirmation" : "Create character"}
            >
              <Icon name="plus" size="sm" />
              Create character
            </button>
          </>
        }
      />
      {error && <ErrorBanner message={error} />}

      {/* Registration handoff */}
      <section className="panel">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="m-0 text-base font-semibold text-wos-text">Beta registration</h2>
            <div className="mt-2 flex flex-wrap gap-2">
              <AutomationChip label="Image code" value={pending?.image_code} />
              <AutomationChip label="Slider" value={pending?.slider} />
              <AutomationChip label="Stage" value={pending?.stage} />
            </div>
          </div>
        </div>
        {pending ? (
          <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-wos-border-subtle bg-wos-panel-raised/40 p-3">
            <div className="min-w-0 flex-1">
              <div className="text-xs font-semibold uppercase tracking-wide text-wos-text-muted">
                Current character
              </div>
              <div className="mt-0.5 truncate text-lg font-semibold text-wos-text">
                {pending.username}
              </div>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-primary"
                disabled={busy}
                onClick={() => sendVerdict("done")}
              >
                Done
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={() => sendVerdict("failed")}
              >
                Failed
              </button>
            </div>
          </div>
        ) : (
          <div className="mt-4 rounded-lg border border-wos-border-subtle bg-wos-panel-raised/30 px-3 py-2 text-sm text-wos-text-secondary">
            Ready for the next beta character.
          </div>
        )}
        {registrationStatus?.log_path || registrationStatus?.log_tail ? (
          <div className="mt-4 rounded-lg border border-wos-border-subtle bg-wos-panel-raised/30 p-3">
            <div className="flex flex-wrap items-center gap-2 text-xs text-wos-text-muted">
              <span>pid {registrationStatus.pid ?? "—"}</span>
              <span>exit {registrationStatus.exit_code ?? "—"}</span>
              <code className="max-w-full truncate rounded bg-wos-surface px-1.5 py-0.5">
                {registrationStatus.log_path ?? "log pending"}
              </code>
              <button
                type="button"
                className="btn-secondary ml-auto inline-flex items-center gap-1 px-2 py-1 text-xs"
                disabled={!registrationStatus.log_tail}
                onClick={copyRegistrationLogs}
              >
                <Icon name="copy" size="sm" />
                {logsCopied ? "Copied" : "Copy logs"}
              </button>
              <button
                type="button"
                className="btn-secondary inline-flex items-center gap-1 px-2 py-1 text-xs text-red-200"
                disabled={busy || registrationRunning || Boolean(pending)}
                onClick={clearRegistrationLogs}
                title={
                  pending || registrationRunning
                    ? "Registration is still active"
                    : "Clear registration log"
                }
              >
                <Icon name="trash" size="sm" />
                Clear logs
              </button>
            </div>
            {registrationStatus.log_tail ? (
              <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-wos-surface p-2 text-xs leading-relaxed text-wos-text-secondary">
                {registrationStatus.log_tail}
              </pre>
            ) : null}
          </div>
        ) : null}
      </section>

      {/* Generate */}
      <section className="panel">
        <h2 className="m-0 text-base font-semibold text-wos-text">Generate accounts</h2>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="muted block">Count</span>
            <input
              type="number"
              min={1}
              max={50}
              value={count}
              onChange={(e) => setCount(Math.max(1, Number(e.target.value) || 1))}
              className="mt-1 w-20 rounded border border-wos-hairline bg-transparent px-2 py-1"
            />
          </label>
          <label className="text-sm">
            <span className="muted block">Seed (optional)</span>
            <input
              type="text"
              value={seed}
              placeholder="reproducible batch"
              onChange={(e) => setSeed(e.target.value)}
              className="mt-1 w-48 rounded border border-wos-hairline bg-transparent px-2 py-1"
            />
          </label>
          <button
            type="button"
            className="btn-secondary inline-flex items-center gap-1.5"
            disabled={busy}
            onClick={generate}
          >
            <Icon name="plus" size="sm" />
            Generate {count}
          </button>
        </div>
      </section>

      {/* Accounts */}
      <section className="panel">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h2 className="m-0 text-base font-semibold text-wos-text">Accounts</h2>
          <span className="farm-count-badge">{accounts.length}</span>
          {shown.length > 0 ? (
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-1.5 px-2.5 py-1 text-xs"
              onClick={toggleAll}
            >
              <Icon
                name="chevron-right"
                size="sm"
                className={`farm-expander__icon${allExpanded ? " farm-expander__icon--open" : ""}`}
              />
              {allExpanded ? "Collapse all" : "Expand all"}
            </button>
          ) : null}
          <div className="ml-auto flex flex-wrap gap-1 text-xs">
            <FilterChip label="all" active={filter === "all"} onClick={() => setFilter("all")} />
            {counts.map((c) => (
              <FilterChip
                key={c.status}
                label={`${c.status} ${c.n}`}
                active={filter === c.status}
                onClick={() => setFilter(c.status)}
              />
            ))}
          </div>
        </div>

        {shown.length === 0 ? (
          <p className="muted mt-3 mb-0">No accounts for this filter.</p>
        ) : (
          <div className="data-table-wrap mt-3">
          <table className="data-table min-w-[980px]">
            <thead>
              {accountsTable.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id}>
                  {headerGroup.headers.map((header) => (
                    <th
                      key={header.id}
                      className={header.column.id === "actions" ? "text-right" : undefined}
                    >
                      {header.isPlaceholder
                        ? null
                        : flexRender(
                            header.column.columnDef.header,
                            header.getContext(),
                          )}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {accountsTable.getRowModel().rows.map((row) => {
                const a = row.original;
                const open = expanded.has(a.username);
                const edit = characterEdits[a.username] ?? {
                  server: "",
                  fid: "",
                  nickname: "",
                };
                return (
                  <Fragment key={a.username}>
                    <tr
                      className={[
                        a.active ? "farm-row--active" : "",
                        open ? "farm-row--open" : "",
                      ]
                        .filter(Boolean)
                        .join(" ") || undefined}
                      title={activeTitle(a.active)}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td
                          key={cell.id}
                          className={
                            cell.column.id === "actions"
                              ? "text-right"
                              : cell.column.id === "expander"
                                ? "farm-expander-cell"
                                : undefined
                          }
                        >
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext(),
                          )}
                        </td>
                      ))}
                    </tr>
                    {open ? (
                    <tr className="sub-row">
                      <td colSpan={accountsTable.getAllLeafColumns().length}>
                        <div className="flex flex-col gap-2 rounded-md border border-wos-border-subtle/50 bg-wos-surface/35 p-2">
                          {a.characters.length > 0 ? (
                            <div className="grid grid-cols-[minmax(8rem,1fr)_minmax(8rem,1fr)_minmax(8rem,1fr)_auto] items-center gap-2 text-wos-text-secondary">
                              <div className="font-semibold uppercase tracking-wide text-wos-text-muted">
                                Server
                              </div>
                              <div className="font-semibold uppercase tracking-wide text-wos-text-muted">
                                FID
                              </div>
                              <div className="font-semibold uppercase tracking-wide text-wos-text-muted">
                                Nickname
                              </div>
                              <div />
                              {a.characters.map((c) => (
                                <Fragment key={`${a.username}:${c.server}`}>
                                  <div
                                    className={
                                      c.active
                                        ? "farm-character-cell--active text-wos-text"
                                        : "text-wos-text"
                                    }
                                    title={activeTitle(c.active)}
                                  >
                                    {c.server}
                                  </div>
                                  <div
                                    className={
                                      c.active
                                        ? "farm-character-cell--active flex items-center gap-1.5 font-mono text-emerald-100"
                                        : "font-mono text-sky-100"
                                    }
                                    title={activeTitle(c.active)}
                                  >
                                    {c.active ? <ActiveMarker active={c.active} /> : null}
                                    <span>{c.fid}</span>
                                  </div>
                                  <div className="text-wos-text">
                                    {c.nickname || "—"}
                                  </div>
                                  <div className="text-right">
                                    <button
                                      type="button"
                                      className="btn-secondary inline-flex items-center gap-1 px-2 py-1 text-xs text-red-200"
                                      disabled={busy}
                                      onClick={() => removeCharacter(a.username, c.server)}
                                      title={`Delete ${c.server}`}
                                    >
                                      <Icon name="trash" size="sm" />
                                      Delete
                                    </button>
                                  </div>
                                </Fragment>
                              ))}
                            </div>
                          ) : (
                            <div className="text-wos-text-muted">
                              No game characters attached yet.
                            </div>
                          )}
                          <div className="flex flex-wrap items-center gap-2">
                            <input
                              type="text"
                              value={edit.server}
                              placeholder="server"
                              onChange={(e) =>
                                updateCharacterEdit(a.username, {
                                  server: e.target.value,
                                })
                              }
                              className="w-32 rounded-md border border-wos-border-subtle bg-wos-input px-2 py-1 text-wos-text"
                            />
                            <input
                              type="text"
                              value={edit.fid}
                              placeholder="fid"
                              onChange={(e) =>
                                updateCharacterEdit(a.username, {
                                  fid: e.target.value,
                                })
                              }
                              className="w-36 rounded-md border border-wos-border-subtle bg-wos-input px-2 py-1 text-wos-text"
                            />
                            <input
                              type="text"
                              value={edit.nickname}
                              placeholder="nickname"
                              onChange={(e) =>
                                updateCharacterEdit(a.username, {
                                  nickname: e.target.value,
                                })
                              }
                              className="w-40 rounded-md border border-wos-border-subtle bg-wos-input px-2 py-1 text-wos-text"
                            />
                            <button
                              type="button"
                              className="btn-secondary inline-flex items-center gap-1 px-2 py-1 text-xs"
                              disabled={busy || !edit.server.trim() || !edit.fid.trim()}
                              onClick={() => saveCharacter(a.username)}
                            >
                              <Icon name="plus" size="sm" />
                              Add character
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </section>
      </div>

      <Dialog
        open={deleteTarget !== null}
        onClose={() => {
          if (!busy) setDeleteTarget(null);
        }}
        className="headless-dialog-root"
      >
        <DialogBackdrop transition className="headless-dialog__backdrop" />
        <div className="headless-dialog__container">
          <DialogPanel transition className="headless-dialog__panel">
            <DialogTitle className="headless-dialog__title">
              Delete {deleteTarget?.username}?
            </DialogTitle>
            <div className="headless-dialog__body">
              <p className="m-0">
                Type the nickname exactly to delete this farm account.
              </p>
              <input
                autoFocus
                type="text"
                value={deleteConfirm}
                onChange={(e) => setDeleteConfirm(e.target.value)}
                placeholder={deleteTarget?.username}
                className="mt-3 w-full rounded-lg border border-wos-border-subtle bg-wos-input px-3 py-2 text-wos-text outline-none focus:border-sky-400/70 focus:ring-2 focus:ring-sky-400/25"
              />
            </div>
            <div className="headless-dialog__actions">
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={() => setDeleteTarget(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary headless-dialog__confirm--danger"
                disabled={busy || !deleteTarget || deleteConfirm !== deleteTarget.username}
                onClick={confirmRemove}
              >
                Delete account
              </button>
            </div>
          </DialogPanel>
        </div>
      </Dialog>
    </>
  );
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "registered" || status === "bound"
      ? "status-idle"
      : status === "failed"
        ? "pill-danger"
        : "status-pending";
  return (
    <span className={`status-pill ${cls}`}>
      <span className="status-pill__dot" />
      {status}
    </span>
  );
}

function ActiveMarker({ active }: { active: ActiveInGame }) {
  return (
    <span
      className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-emerald-300/50 bg-emerald-400/15 text-emerald-200"
      title={activeTitle(active)}
      aria-label="In game"
    >
      <Icon name="play" size="sm" />
    </span>
  );
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-2 py-0.5 ${
        active
          ? "border-wos-text/40 bg-wos-surface text-wos-text"
          : "border-wos-hairline muted"
      }`}
    >
      {label}
    </button>
  );
}

function AutomationChip({ label, value }: { label: string; value?: string }) {
  const clean = (value || "pending").replaceAll("_", " ");
  const state = (value || "").toLowerCase();
  const tone =
    state === "solved" || state === "dragged" || state === "awaiting_submit"
      ? "border-emerald-400/40 bg-emerald-500/15 text-emerald-200"
      : state === "failed" || state === "skipped"
        ? "border-amber-400/40 bg-amber-500/15 text-amber-200"
        : "border-wos-hairline bg-wos-surface/40 text-wos-text";
  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs ${tone}`}>
      <span className="muted mr-1">{label}</span>
      <strong className="font-semibold">{clean}</strong>
    </span>
  );
}
