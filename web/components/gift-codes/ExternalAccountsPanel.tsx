"use client";

import { useCallback, useEffect, useState } from "react";

import { AppTabs } from "@/components/headless";
import {
  type ExternalAccount,
  FeatureLockedError,
  deleteExternalAccount,
  fetchExternalAccounts,
  toggleExternalAccount,
  upsertExternalAccount,
} from "@/lib/api";

export type ExternalAccountsGame = { id: string; label: string };

type AddRow = { player_id: number; label: string };

// Accepts either ``fid`` on its own or ``fid<TAB|space>label``.
function parseBulk(text: string): { rows: AddRow[]; errors: string[] } {
  const rows: AddRow[] = [];
  const errors: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const parts = line.split(/\s+/);
    const idStr = parts[0] ?? "";
    const id = Number(idStr);
    if (!Number.isInteger(id) || id <= 0) {
      errors.push(`${idStr || "(empty)"}: not a valid fid`);
      continue;
    }
    rows.push({ player_id: id, label: parts.slice(1).join(" ") });
  }
  return { rows, errors };
}

function PanelTitle({ accountsCount }: { accountsCount: number }) {
  return (
    <h2 className="m-0 mb-3 flex flex-wrap items-center gap-2 text-base font-semibold text-wos-text">
      <span>External accounts</span>
      <span
        className="rounded-full border border-amber-400/40 bg-amber-500/15 px-1.5 py-0 text-[10px] font-semibold uppercase tracking-wide text-amber-300"
        title="Requires PRO license"
      >
        PRO
      </span>
      {accountsCount ? (
        <span className="text-sm font-normal text-wos-text-muted">
          · {accountsCount}
        </span>
      ) : null}
    </h2>
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
  const [view, setView] = useState<{
    licensed: boolean;
    accounts: ExternalAccount[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bulk, setBulk] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchExternalAccounts(game);
      setView({ licensed: data.feature_licensed, accounts: data.accounts });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [game]);

  useEffect(() => {
    void load();
  }, [load]);

  const onAdd = async () => {
    setStatus(null);
    setError(null);
    const { rows, errors } = parseBulk(bulk);
    if (errors.length) {
      setError(errors.join(", "));
      return;
    }
    if (!rows.length) {
      setError("Nothing to add — paste one fid per line (optionally fid<space>label).");
      return;
    }
    setBusy(true);
    let added = 0;
    let failed = 0;
    const messages: string[] = [];
    try {
      for (const row of rows) {
        try {
          await upsertExternalAccount(game, {
            player_id: row.player_id,
            label: row.label || undefined,
            // validate_fid=true → API hits /api/player to confirm the fid
            // and auto-populates the nickname.
            validate_fid: true,
          });
          added += 1;
        } catch (e) {
          failed += 1;
          if (e instanceof FeatureLockedError) {
            // No point continuing — the Pro gate just rejected us. Surface
            // the upsell and stop.
            setError(e.message);
            return;
          }
          messages.push(`${row.player_id}: ${e instanceof Error ? e.message : String(e)}`);
        }
      }
      const summary = `Added ${added}, failed ${failed}`;
      setStatus(messages.length ? `${summary} — ${messages.join("; ")}` : summary);
      setBulk("");
      await load();
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

  const onDelete = async (acc: ExternalAccount) => {
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Delete external account ${acc.player_id} (${acc.nickname || "no nickname"})?`)
    ) {
      return;
    }
    setStatus(null);
    setError(null);
    setBusy(true);
    try {
      await deleteExternalAccount(game, acc.player_id);
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

  if (view === null && !error) {
    return (
      <section className="panel panel--spaced">
        <PanelTitle accountsCount={0} />
        {showTabs ? (
          <AppTabs
            tabs={tabs}
            selectedKey={game}
            onChange={setGame}
            renderPanels={false}
          />
        ) : null}
        <p className="muted">Loading…</p>
      </section>
    );
  }

  const licensed = view?.licensed ?? false;
  const accounts = view?.accounts ?? [];

  return (
    <section className="panel panel--spaced">
      <PanelTitle accountsCount={accounts.length} />
      {showTabs ? (
        <AppTabs
          tabs={tabs}
          selectedKey={game}
          onChange={setGame}
          renderPanels={false}
        />
      ) : null}
      <p className="muted">
        Redeem this game&apos;s gift codes for accounts the bot does not own —
        alliance members, partner farms, secondary accounts on other hardware.
        Scope: <code>{game}</code>.
      </p>

      {!licensed ? (
        <div className="mb-4 rounded-lg border border-amber-400/30 bg-amber-500/10 p-3 text-sm">
          <strong className="text-amber-300">PRO feature required.</strong>{" "}
          <span className="text-wos-text-secondary">
            {accounts.length > 0
              ? `Your license used to include this feature — the ${accounts.length} existing row(s) below are read-only and won't be processed until you upgrade.`
              : "Upgrade to PRO to add external accounts. License feature flag: "}
            {accounts.length === 0 ? (
              <code>gift_codes.external_accounts</code>
            ) : null}
          </span>
        </div>
      ) : null}

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <p className="muted">{status}</p> : null}

      {licensed ? (
        <div className="mb-4">
          <label
            htmlFor="bulk-add"
            className="muted block text-xs uppercase tracking-wide"
          >
            Add accounts (one per line: <code>fid</code> or{" "}
            <code>fid label</code>)
          </label>
          <textarea
            id="bulk-add"
            rows={4}
            value={bulk}
            onChange={(e) => setBulk(e.target.value)}
            disabled={busy}
            placeholder="401227964 Alliance: PHX/r24&#10;555000111 Farm-3"
            className="mt-1 w-full rounded-lg border border-wos-border-subtle bg-wos-input p-2 font-mono text-sm text-wos-text focus:border-sky-400/70 focus:outline-none focus:ring-2 focus:ring-sky-400/25"
          />
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              className="btn-primary"
              disabled={busy || !bulk.trim()}
              onClick={onAdd}
            >
              {busy ? "Adding…" : "Add accounts"}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={load}
              disabled={busy}
            >
              Reload
            </button>
          </div>
        </div>
      ) : null}

      {accounts.length > 0 ? (
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>fid</th>
                <th>Nickname</th>
                <th>Label</th>
                <th>Enabled</th>
                <th>Added</th>
                <th>Last seen</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.player_id} className={a.enabled ? undefined : "row-disabled"}>
                  <td>
                    <code>{a.player_id}</code>
                  </td>
                  <td>{a.nickname || "—"}</td>
                  <td>{a.label || "—"}</td>
                  <td>
                    <span className={`status-pill ${a.enabled ? "pill-live" : "pill-paused"}`}>
                      {a.enabled ? "enabled" : "disabled"}
                    </span>
                  </td>
                  <td>{fmtDate(a.added_at)}</td>
                  <td>{fmtDate(a.last_seen_at)}</td>
                  <td>
                    {licensed ? (
                      <div className="flex gap-1">
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => onToggle(a)}
                          disabled={busy}
                        >
                          {a.enabled ? "Disable" : "Enable"}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => onDelete(a)}
                          disabled={busy}
                        >
                          Delete
                        </button>
                      </div>
                    ) : (
                      <span className="meta">read-only</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : licensed ? (
        <p className="meta">No external accounts yet.</p>
      ) : null}
    </section>
  );
}
