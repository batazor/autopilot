"use client";

import {
  Fragment,
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { AppConfirmDialog, AppTabs } from "@/components/headless";
import { MetricCard, MetricGrid, PageLoading } from "@/components/ui";
import {
  type ExternalAccount,
  type ExternalAccountCode,
  externalAccountRedeemStreamUrl,
  deleteExternalAccount,
  fetchExternalAccountCodes,
  fetchExternalAccounts,
  redeemGiftCodes,
  toggleExternalAccount,
  upsertExternalAccount,
} from "@/lib/api";

export type ExternalAccountsGame = { id: string; label: string };

function CodeStatusPill({ c }: { c: ExternalAccountCode }) {
  const cls = c.redeemed
    ? "pill-live"
    : c.slot_expired
      ? "pill-paused"
      : c.needs_run
        ? "pill-offline"
        : "pill-paused";
  return <span className={`status-pill ${cls}`}>{c.status}</span>;
}

// Expanded child row under one external account: per-code status table plus a
// "Run now" button that streams redeem progress (SSE) into a progress bar.
function AccountCodesRow({
  game,
  playerId,
  colSpan,
  onRedeemed,
}: {
  game: string;
  playerId: number;
  colSpan: number;
  onRedeemed: () => void;
}) {
  const [codes, setCodes] = useState<ExternalAccountCode[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number; message: string } | null>(
    null,
  );
  const [runError, setRunError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const loadCodes = useCallback(async () => {
    try {
      const data = await fetchExternalAccountCodes(game, playerId);
      setCodes(data.codes);
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, [game, playerId]);

  useEffect(() => {
    void loadCodes();
    return () => {
      esRef.current?.close();
    };
  }, [loadCodes]);

  const run = () => {
    if (running) return;
    setRunError(null);
    setProgress({ done: 0, total: 0, message: "starting…" });
    setRunning(true);
    const es = new EventSource(externalAccountRedeemStreamUrl(game, playerId));
    esRef.current = es;
    es.onmessage = (ev) => {
      let data: { type: string; done?: number; total?: number; message?: string };
      try {
        data = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (data.type === "progress") {
        setProgress({ done: data.done ?? 0, total: data.total ?? 0, message: data.message ?? "" });
      } else if (data.type === "done") {
        es.close();
        setRunning(false);
        void loadCodes();
        onRedeemed();
      } else if (data.type === "error") {
        es.close();
        setRunning(false);
        setRunError(data.message ?? "redeem failed");
      }
    };
    es.onerror = () => {
      es.close();
      setRunning(false);
      setRunError((prev) => prev ?? "connection error during redeem");
    };
  };

  const pending = codes?.filter((c) => c.needs_run).length ?? 0;
  const pct =
    progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <tr>
      <td colSpan={colSpan} className="bg-wos-panel-raised/40">
        <div className="flex flex-col gap-3 p-2">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xs uppercase tracking-wide text-wos-text-muted">
              Gift codes for {playerId}
            </span>
            <button
              type="button"
              className="btn-primary px-2 py-1 text-xs"
              onClick={run}
              disabled={running}
              title={pending ? `${pending} code(s) need redeeming` : "Re-run redeem for this account"}
            >
              {running ? "Running…" : `Run now ${pending ? `(${pending})` : ""}`}
            </button>
            {progress ? (
              <span className="meta">
                {progress.total > 0 ? `${progress.done}/${progress.total}` : "—"} · {progress.message}
              </span>
            ) : null}
          </div>

          {progress && (running || pct > 0) ? (
            <div
              className="h-2 w-full overflow-hidden rounded-full bg-wos-border-subtle"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className={`h-full rounded-full bg-sky-400 transition-[width] duration-300 ${running && progress.total === 0 ? "animate-pulse" : ""}`}
                style={{ width: `${running && progress.total === 0 ? 100 : pct}%` }}
              />
            </div>
          ) : null}

          {runError ? <div className="error-banner">{runError}</div> : null}
          {loadError ? <div className="error-banner">{loadError}</div> : null}

          {codes === null ? (
            <p className="muted">Loading codes…</p>
          ) : codes.length === 0 ? (
            <p className="meta">No gift codes known yet.</p>
          ) : (
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Gift code</th>
                    <th>Status</th>
                    <th>Expires</th>
                    <th>Needs run</th>
                  </tr>
                </thead>
                <tbody>
                  {codes.map((c) => (
                    <tr key={c.code} className={c.slot_expired ? "row-disabled" : undefined}>
                      <td>
                        <code>{c.code}</code>
                      </td>
                      <td>
                        <CodeStatusPill c={c} />
                      </td>
                      <td className="meta">{c.expires}</td>
                      <td>{c.needs_run ? "yes" : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

function PanelTitle({ accountsCount }: { accountsCount: number }) {
  return (
    <h2 className="m-0 mb-3 flex flex-wrap items-center gap-2 text-base font-semibold text-wos-text">
      <span>External accounts</span>
      {accountsCount ? (
        <span className="text-sm font-normal text-wos-text-muted">
          · {accountsCount}
        </span>
      ) : null}
    </h2>
  );
}

// Summary status bar — same mini metric cards the gift-codes page uses, so the
// panel reads as one cohesive screen with the standard gamer view above it.
function StatusBar({
  total,
  enabled,
}: {
  total: number;
  enabled: number;
}) {
  const items = [
    { label: "Accounts", value: total },
    { label: "Enabled", value: enabled },
    { label: "Disabled", value: total - enabled },
  ];
  return (
    <MetricGrid minColWidth="7rem" className="mb-4">
      {items.map((it) => (
        <MetricCard key={it.label} label={it.label} value={it.value} />
      ))}
    </MetricGrid>
  );
}

function fmtDate(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

export function ExternalAccountsPanel({
  games,
  initialGame,
  game: controlledGame,
  onGameChange,
}: {
  games: ExternalAccountsGame[];
  initialGame?: string;
  /** When set, the parent owns the tab state and the panel won't render its
   *  own tab strip. Use this when the game selector lives outside the panel
   *  (e.g. lifted to the page header). */
  game?: string;
  onGameChange?: (next: string) => void;
}) {
  const [uncontrolledGame, setUncontrolledGame] = useState<string>(
    () => initialGame ?? games[0]?.id ?? "wos",
  );
  const isControlled = controlledGame !== undefined;
  const game = isControlled ? controlledGame : uncontrolledGame;
  const setGame = (next: string) => {
    if (isControlled) {
      onGameChange?.(next);
    } else {
      setUncontrolledGame(next);
    }
  };

  const [accounts, setAccounts] = useState<ExternalAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Create form — only the gamer id is required; the API resolves the nickname
  // via validate_fid and the label is an optional operator note.
  const [newId, setNewId] = useState("");
  const [newLabel, setNewLabel] = useState("");

  // Inline update (edit the label of an existing row).
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editLabel, setEditLabel] = useState("");

  // Which account's per-code child table is expanded (one at a time).
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ExternalAccount | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchExternalAccounts(game);
      setAccounts(data.accounts);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [game]);

  useEffect(() => {
    void load();
  }, [load]);

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    setStatus(null);
    setError(null);
    const id = Number(newId.trim());
    if (!Number.isInteger(id) || id <= 0) {
      setError("Enter a valid gamer id (positive number).");
      return;
    }
    setBusy(true);
    try {
      // validate_fid=true → API confirms the fid and auto-populates nickname.
      await upsertExternalAccount(game, {
        player_id: id,
        label: newLabel.trim() || undefined,
        validate_fid: true,
      });
      setNewId("");
      setNewLabel("");
      await load();
      // Add-then-run: immediately redeem so the new account picks up the
      // current gift codes without a separate click. The add already
      // succeeded, so a redeem failure is surfaced without losing the row.
      setStatus(`Added ${id} — running redeem…`);
      try {
        const redeem = await redeemGiftCodes(game);
        setStatus(
          redeem.already_running
            ? `Added ${id}; redeem is already running.`
            : `Added ${id} and ran redeem.`,
        );
      } catch (err) {
        setStatus(
          `Added ${id}, but redeem failed: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onToggle = async (acc: ExternalAccount) => {
    setStatus(null);
    setError(null);
    setBusy(true);
    try {
      await toggleExternalAccount(game, acc.player_id, !acc.enabled);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const beginEdit = (acc: ExternalAccount) => {
    setStatus(null);
    setError(null);
    setEditingId(acc.player_id);
    setEditLabel(acc.label ?? "");
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditLabel("");
  };

  const saveEdit = async (acc: ExternalAccount) => {
    setBusy(true);
    setError(null);
    try {
      // validate_fid=false → label-only update, skip the /api/player round-trip.
      await upsertExternalAccount(game, {
        player_id: acc.player_id,
        label: editLabel.trim() || undefined,
        enabled: acc.enabled,
        validate_fid: false,
      });
      cancelEdit();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    const acc = deleteTarget;
    if (!acc) return;
    setStatus(null);
    setError(null);
    setBusy(true);
    try {
      await deleteExternalAccount(game, acc.player_id);
      setDeleteTarget(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // Hide the in-panel tab strip when the parent renders its own selector.
  const showTabs = games.length > 1 && !isControlled;
  const tabs = games.map((g) => ({ key: g.id, label: g.label, title: g.id }));

  const TabStrip = showTabs ? (
    <AppTabs tabs={tabs} selectedKey={game} onChange={setGame} renderPanels={false} />
  ) : null;

  if (accounts === null && !error) {
    return (
      <section className="panel panel--spaced">
        <PanelTitle accountsCount={0} />
        {TabStrip}
        <PageLoading />
      </section>
    );
  }

  const rows = accounts ?? [];
  const enabledCount = rows.filter((a) => a.enabled).length;

  return (
    <section className="panel panel--spaced">
      <PanelTitle accountsCount={rows.length} />
      {TabStrip}
      <p className="muted">
        Redeem this game&apos;s gift codes for accounts the bot does not own —
        alliance members, partner farms, secondary accounts on other hardware.
        Scope: <code>{game}</code>.
      </p>

      <StatusBar total={rows.length} enabled={enabledCount} />

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <p className="muted">{status}</p> : null}

      <form onSubmit={onCreate} className="mb-4 flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1">
          <label
            htmlFor="ext-add-id"
            className="muted text-xs uppercase tracking-wide"
          >
            Gamer ID (fid) <span className="text-amber-300">*</span>
          </label>
          <input
            id="ext-add-id"
            inputMode="numeric"
            required
            value={newId}
            onChange={(e) => setNewId(e.target.value.replace(/[^0-9]/g, ""))}
            disabled={busy}
            placeholder="401227964"
            className="field w-40 font-mono"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label
            htmlFor="ext-add-label"
            className="muted text-xs uppercase tracking-wide"
          >
            Label (optional)
          </label>
          <input
            id="ext-add-label"
            value={newLabel}
            onChange={(e) => setNewLabel(e.target.value)}
            disabled={busy}
            placeholder="Alliance: PHX / Farm-3"
            className="field w-56"
          />
        </div>
        <button
          type="submit"
          className="btn-primary"
          disabled={busy || !newId.trim()}
        >
          {busy ? "Working…" : "Add & redeem"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={load}
          disabled={busy}
        >
          Reload
        </button>
      </form>

      {rows.length > 0 ? (
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>fid</th>
                <th>Nickname</th>
                <th>Label</th>
                <th>Status</th>
                <th>Added</th>
                <th>Last seen</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => {
                const editing = editingId === a.player_id;
                const expanded = expandedId === a.player_id;
                return (
                  <Fragment key={a.player_id}>
                    <tr className={a.enabled ? undefined : "row-disabled"}>
                      <td>
                        <button
                          type="button"
                          className="btn-secondary mr-1.5 px-1.5 py-0.5 text-xs"
                          aria-expanded={expanded}
                          title={expanded ? "Hide gift codes" : "Show gift codes"}
                          onClick={() => setExpandedId(expanded ? null : a.player_id)}
                        >
                          {expanded ? "▾" : "▸"}
                        </button>
                        <code>{a.player_id}</code>
                      </td>
                    <td>{a.nickname || "—"}</td>
                    <td>
                      {editing ? (
                        <input
                          autoFocus
                          value={editLabel}
                          onChange={(e) => setEditLabel(e.target.value)}
                          disabled={busy}
                          className="field w-48"
                        />
                      ) : (
                        a.label || "—"
                      )}
                    </td>
                    <td>
                      <span
                        className={`status-pill ${a.enabled ? "pill-live" : "pill-paused"}`}
                      >
                        {a.enabled ? "enabled" : "disabled"}
                      </span>
                    </td>
                    <td>{fmtDate(a.added_at)}</td>
                    <td>{fmtDate(a.last_seen_at)}</td>
                    <td>
                      {editing ? (
                        <div className="flex justify-end gap-1">
                          <button
                            type="button"
                            className="btn-primary px-2 py-1 text-xs"
                            onClick={() => saveEdit(a)}
                            disabled={busy}
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            className="btn-secondary px-2 py-1 text-xs"
                            onClick={cancelEdit}
                            disabled={busy}
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="flex justify-end gap-1">
                          <button
                            type="button"
                            className="btn-secondary px-2 py-1 text-xs"
                            onClick={() => onToggle(a)}
                            disabled={busy}
                          >
                            {a.enabled ? "Disable" : "Enable"}
                          </button>
                          <button
                            type="button"
                            className="btn-secondary px-2 py-1 text-xs"
                            onClick={() => beginEdit(a)}
                            disabled={busy}
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            className="btn-secondary px-2 py-1 text-xs"
                            onClick={() => setDeleteTarget(a)}
                            disabled={busy}
                          >
                            Delete
                          </button>
                        </div>
                      )}
                      </td>
                    </tr>
                    {expanded ? (
                      <AccountCodesRow
                        game={game}
                        playerId={a.player_id}
                        colSpan={7}
                        onRedeemed={load}
                      />
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="meta">No external accounts yet — add one above.</p>
      )}

      <AppConfirmDialog
        open={deleteTarget !== null}
        title="Delete external account"
        confirmLabel={busy ? "Deleting…" : "Delete"}
        variant="danger"
        busy={busy}
        onClose={() => {
          if (!busy) setDeleteTarget(null);
        }}
        onConfirm={confirmDelete}
      >
        <p className="m-0 text-sm text-wos-text-muted">
          Delete external account <code>{deleteTarget?.player_id}</code>
          {deleteTarget?.nickname ? ` (${deleteTarget.nickname})` : ""}? This removes it
          from gift-code redemption.
        </p>
      </AppConfirmDialog>
    </section>
  );
}
