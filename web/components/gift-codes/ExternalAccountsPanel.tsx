"use client";

import { useCallback, useEffect, useState } from "react";

import {
  type ExternalAccount,
  FeatureLockedError,
  deleteExternalAccount,
  fetchExternalAccounts,
  toggleExternalAccount,
  upsertExternalAccount,
} from "@/lib/api";

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

function fmtDate(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

export function ExternalAccountsPanel({ game }: { game: string }) {
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

  if (view === null && !error) {
    return (
      <section className="panel panel--spaced">
        <h2>External accounts</h2>
        <p className="meta">Loading…</p>
      </section>
    );
  }

  const licensed = view?.licensed ?? false;
  const accounts = view?.accounts ?? [];

  return (
    <section className="panel panel--spaced">
      <h2>External accounts (Pro){accounts.length ? ` · ${accounts.length}` : ""}</h2>
      <p className="meta">
        Redeem this game&apos;s gift codes for accounts the bot does not own —
        alliance members, partner farms, secondary accounts on other hardware.
        Scope: <code>{game}</code>.
      </p>

      {!licensed ? (
        <div className="panel" style={{ background: "var(--bg-secondary)", marginBottom: "1rem" }}>
          <strong>Pro feature required.</strong>{" "}
          {accounts.length > 0
            ? `Your license used to include this feature — the ${accounts.length} existing row(s) below are read-only and won't be processed until you upgrade.`
            : "Upgrade to Pro to add external accounts. License key feature flag: "}
          {accounts.length === 0 ? <code>gift_codes.external_accounts</code> : null}
        </div>
      ) : null}

      {error ? <div className="error-banner">{error}</div> : null}
      {status ? <p className="meta">{status}</p> : null}

      {licensed ? (
        <div style={{ marginBottom: "1rem" }}>
          <label htmlFor="bulk-add" className="meta" style={{ display: "block" }}>
            Add accounts (one per line: <code>fid</code> or <code>fid label</code>)
          </label>
          <textarea
            id="bulk-add"
            rows={4}
            value={bulk}
            onChange={(e) => setBulk(e.target.value)}
            disabled={busy}
            placeholder="401227964 Alliance: PHX/r24&#10;555000111 Farm-3"
            style={{ width: "100%", fontFamily: "monospace", marginTop: "0.25rem" }}
          />
          <div className="toolbar" style={{ marginTop: "0.5rem" }}>
            <button
              type="button"
              className="btn-primary"
              disabled={busy || !bulk.trim()}
              onClick={onAdd}
            >
              {busy ? "Adding…" : "Add accounts"}
            </button>
            <button type="button" className="btn-secondary" onClick={load} disabled={busy}>
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
                      <div className="toolbar" style={{ gap: "0.25rem", margin: 0 }}>
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
