"use client";

import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { Fragment } from "react";
import { Button, Chip, Icon } from "@/components/ui";
import { type FarmAccount, activeTitle } from "@/lib/farm/types";
import { AccountOptions } from "./AccountOptions";
import { ActiveMarker } from "./ActiveMarker";
import { RoleBadge } from "./RoleBadge";
import { StatusBadge } from "./StatusBadge";
import type { FarmState } from "./useFarmState";

const EMPTY_EDIT = { server: "", fid: "", nickname: "" };

/** Accounts panel: status filters, the accounts table, and expandable character sub-rows. */
export function AccountsTable({ farm }: { farm: FarmState }) {
  const {
    accounts,
    shown,
    filter,
    setFilter,
    counts,
    allExpanded,
    toggleAll,
    expanded,
    toggleExpanded,
    secrets,
    busy,
    reveal,
    bind,
    setBindEdits,
    canStartRegistration,
    startRegistration,
    remove,
    characterEdits,
    updateCharacterEdit,
    saveCharacter,
    removeCharacter,
    roles,
    setCharacterRole,
  } = farm;

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
            <Button
              className="inline-flex items-center gap-1 px-2 py-1 text-xs"
              onClick={() => navigator.clipboard?.writeText(secrets[a.username])}
            >
              <Icon name="copy" size="sm" />
              Copy
            </Button>
          </span>
        ) : (
          <Button
            className="px-2 py-1 text-xs"
            disabled={busy}
            onClick={() => reveal(a.username)}
          >
            Reveal
          </Button>
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
              className="field w-40"
            />
            <Button
              className="px-2 py-1 text-xs"
              disabled={busy}
              onClick={() => bind(a.username)}
            >
              Bind
            </Button>
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
              <Button
                variant="primary"
                className="inline-flex items-center gap-1 px-2 py-1 text-xs"
                disabled={!canStartRegistration}
                onClick={() => startRegistration({ username: a.username, existing: true })}
                title={`Register ${a.username}`}
              >
                <Icon name="play" size="sm" />
                Register
              </Button>
            ) : null}
            <Button
              className="inline-flex items-center gap-1 px-2 py-1 text-xs text-red-200"
              disabled={busy}
              onClick={() => remove(a)}
            >
              <Icon name="trash" size="sm" />
              Delete
            </Button>
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
    <section className="panel">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 className="m-0 text-base font-semibold text-wos-text">Accounts</h2>
        <span className="farm-count-badge">{accounts.length}</span>
        {shown.length > 0 ? (
          <Button
            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs"
            onClick={toggleAll}
          >
            <Icon
              name="chevron-right"
              size="sm"
              className={`farm-expander__icon${allExpanded ? " farm-expander__icon--open" : ""}`}
            />
            {allExpanded ? "Collapse all" : "Expand all"}
          </Button>
        ) : null}
        <div className="ml-auto flex flex-wrap gap-1 text-xs">
          <Chip active={filter === "all"} onClick={() => setFilter("all")}>
            all
          </Chip>
          {counts.map((c) => (
            <Chip
              key={c.status}
              active={filter === c.status}
              onClick={() => setFilter(c.status)}
            >
              {`${c.status} ${c.n}`}
            </Chip>
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
                        : flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {accountsTable.getRowModel().rows.map((row) => {
                const a = row.original;
                const open = expanded.has(a.username);
                const edit = characterEdits[a.username] ?? EMPTY_EDIT;
                return (
                  <Fragment key={a.username}>
                    <tr
                      className={
                        [a.active ? "farm-row--active" : "", open ? "farm-row--open" : ""]
                          .filter(Boolean)
                          .join(" ") || undefined
                      }
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
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                    {open ? (
                      <tr className="sub-row">
                        <td colSpan={accountsTable.getAllLeafColumns().length}>
                          <div className="flex flex-col gap-2 rounded-md border border-wos-border-subtle/50 bg-wos-surface/35 p-2">
                            {a.characters.length > 0 ? (
                              <div className="grid grid-cols-[minmax(8rem,1fr)_minmax(8rem,1fr)_minmax(8rem,1fr)_auto_auto] items-center gap-2 text-wos-text-secondary">
                                <div className="font-semibold uppercase tracking-wide text-wos-text-muted">
                                  Server
                                </div>
                                <div className="font-semibold uppercase tracking-wide text-wos-text-muted">
                                  FID
                                </div>
                                <div className="font-semibold uppercase tracking-wide text-wos-text-muted">
                                  Nickname
                                </div>
                                <div className="font-semibold uppercase tracking-wide text-wos-text-muted">
                                  Profile
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
                                    <div className="flex min-w-0 items-center gap-1.5 text-wos-text">
                                      <span className="truncate">{c.nickname || "—"}</span>
                                      <Button
                                        className="inline-flex h-7 w-7 items-center justify-center p-0"
                                        disabled={!c.nickname || busy}
                                        onClick={() => navigator.clipboard?.writeText(c.nickname)}
                                        title={c.nickname ? `Copy nickname ${c.nickname}` : "No nickname"}
                                        aria-label={c.nickname ? `Copy nickname ${c.nickname}` : "No nickname"}
                                      >
                                        <Icon name="copy" size="sm" />
                                      </Button>
                                    </div>
                                    <div className="flex items-center">
                                      <RoleBadge
                                        role={c.role}
                                        roles={roles}
                                        disabled={busy || !c.fid}
                                        onChange={(roleId) =>
                                          setCharacterRole(a.username, c.fid, roleId)
                                        }
                                      />
                                    </div>
                                    <div className="text-right">
                                      <Button
                                        className="inline-flex items-center gap-1 px-2 py-1 text-xs text-red-200"
                                        disabled={busy}
                                        onClick={() => removeCharacter(a.username, c.server)}
                                        title={`Delete ${c.server}`}
                                      >
                                        <Icon name="trash" size="sm" />
                                        Delete
                                      </Button>
                                    </div>
                                  </Fragment>
                                ))}
                              </div>
                            ) : (
                              <div className="text-wos-text-muted">
                                No game characters attached yet.
                              </div>
                            )}
                            {a.characters.some((c) => c.fid) ? (
                              <div className="flex flex-col gap-2">
                                {a.characters
                                  .filter((c) => c.fid)
                                  .map((c) => (
                                    <div
                                      key={`opts:${a.username}:${c.server}`}
                                      className="rounded-md border border-wos-border-subtle/40 bg-wos-surface/20 p-2"
                                    >
                                      <div className="mb-1.5 text-xs font-medium text-wos-text-secondary">
                                        Options ·{" "}
                                        <span className="text-wos-text-muted">
                                          {c.nickname || c.server}
                                        </span>
                                      </div>
                                      <AccountOptions
                                        username={a.username}
                                        fid={c.fid}
                                        disabled={busy}
                                      />
                                    </div>
                                  ))}
                              </div>
                            ) : null}
                            <div className="flex flex-wrap items-center gap-2">
                              <input
                                type="text"
                                value={edit.server}
                                placeholder="server"
                                onChange={(e) =>
                                  updateCharacterEdit(a.username, { server: e.target.value })
                                }
                                className="field w-32"
                              />
                              <input
                                type="text"
                                value={edit.fid}
                                placeholder="fid"
                                onChange={(e) =>
                                  updateCharacterEdit(a.username, { fid: e.target.value })
                                }
                                className="field w-36"
                              />
                              <input
                                type="text"
                                value={edit.nickname}
                                placeholder="nickname"
                                onChange={(e) =>
                                  updateCharacterEdit(a.username, { nickname: e.target.value })
                                }
                                className="field w-40"
                              />
                              <Button
                                className="inline-flex items-center gap-1 px-2 py-1 text-xs"
                                disabled={busy || !edit.server.trim() || !edit.fid.trim()}
                                onClick={() => saveCharacter(a.username)}
                              >
                                <Icon name="plus" size="sm" />
                                Add character
                              </Button>
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
  );
}
