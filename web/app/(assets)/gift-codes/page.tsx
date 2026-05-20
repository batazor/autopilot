"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import {
  fetchGiftCodes,
  redeemGiftCodes,
  scrapeGiftCodes,
} from "@/lib/api";
import type { GiftCodeRow } from "@/lib/wiki";

const STATUS_CLASS: Record<string, string> = {
  PENDING: "pill-paused",
  SUCCESS: "pill-live",
  ALREADY_RECEIVED: "pill-live",
  CDK_EXPIRED: "pill-offline",
  CDK_NOT_FOUND: "pill-offline",
  STOVE_LEVEL_TOO_LOW: "pill-danger",
  FAILED: "pill-danger",
};

function GiftCodesTable({
  rows,
  playerIds,
  title,
}: {
  rows: GiftCodeRow[];
  playerIds: string[];
  title: string;
}) {
  if (!rows.length) return null;
  return (
    <section className="panel panel--spaced">
      <h2>{title}</h2>
      <div className="data-table-wrap">
        <table className="data-table gift-codes-table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Expires</th>
              <th>Expired</th>
              <th>Needs run</th>
              <th>API err</th>
              {playerIds.map((pid) => (
                <th key={pid}>{pid}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.code}
                className={r.slot_expired ? "gift-row-expired" : undefined}
              >
                <td>
                  <code>{r.code}</code>
                </td>
                <td>{r.expires}</td>
                <td>{r.slot_expired ? "yes" : "no"}</td>
                <td>{r.needs_run ? "yes" : "no"}</td>
                <td>{r.api_err}</td>
                {playerIds.map((pid) => {
                  const p = r.players[pid];
                  const st = p?.status ?? "—";
                  const cls = STATUS_CLASS[st] ?? "pill-offline";
                  return (
                    <td key={pid}>
                      <span className={`status-pill ${cls}`} title={p?.label}>
                        {st}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function GiftCodesPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchGiftCodes>> | null>(
    null,
  );
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchGiftCodes(filter));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  const runAction = async (action: "scrape" | "redeem") => {
    setBusy(true);
    setMessage(null);
    try {
      if (action === "scrape") {
        const res = await scrapeGiftCodes();
        setMessage(
          res.count
            ? `Found ${res.count} new code(s): ${res.new_codes.join(", ")}`
            : "No new codes.",
        );
      } else {
        await redeemGiftCodes();
        setMessage("Redeem finished.");
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const m = data?.metrics;

  return (
    <>
      <PageHeader title="Gift codes" />
      <p className="meta">
        Century Game promo codes · file <code>{data?.codes_path ?? "db/giftCodes.yaml"}</code>
      </p>
      {error ? <div className="error-banner">{error}</div> : null}
      {message ? <div className="panel" style={{ marginBottom: "1rem" }}>{message}</div> : null}

      {data?.parse_error ? (
        <div className="error-banner">YAML error: {data.parse_error}</div>
      ) : null}
      {data?.missing_codes_file ? (
        <p className="meta">Codes file missing — run Scrape.</p>
      ) : null}

      <div className="toolbar">
        <button
          type="button"
          className="btn-secondary"
          disabled={busy}
          onClick={() => runAction("scrape")}
        >
          Scrape now
        </button>
        <button
          type="button"
          className="btn-primary"
          disabled={busy}
          onClick={() => runAction("redeem")}
        >
          Redeem now
        </button>
        <input
          type="search"
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <button type="button" className="btn-secondary" onClick={load}>
          Reload
        </button>
      </div>

      {m ? (
        <div className="metrics-row">
          <div className="metric-card">
            <div className="label">Active</div>
            <div className="value">{m.active}</div>
          </div>
          <div className="metric-card">
            <div className="label">Needs run</div>
            <div className="value">{m.needs_run}</div>
          </div>
          <div className="metric-card">
            <div className="label">Pending slots</div>
            <div className="value">{m.pending_slots}</div>
          </div>
          <div className="metric-card">
            <div className="label">Expired</div>
            <div className="value">{m.expired}</div>
          </div>
        </div>
      ) : null}

      {data ? (
        <>
          <GiftCodesTable
            rows={data.active}
            playerIds={data.player_ids}
            title={`Active codes (${data.active.length})`}
          />
          {data.expired.length > 0 ? (
            <GiftCodesTable
              rows={data.expired}
              playerIds={data.player_ids}
              title={`Expired (${data.expired.length})`}
            />
          ) : null}
        </>
      ) : null}
    </>
  );
}
