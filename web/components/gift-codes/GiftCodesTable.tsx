import type { GiftCodeRow } from "@/lib/wiki";
import { STATUS_CLASS, STATUS_HELP, STATUS_SHORT } from "@/lib/gift-codes/types";
import { CopyableCode } from "./CopyableCode";

export function GiftCodesTable({
  rows,
  playerIds,
  title,
  onDeleteCode,
}: {
  rows: GiftCodeRow[];
  playerIds: string[];
  title: string;
  // When provided (manual-entry builds), each row gets a remove button so a
  // mistyped code can be dropped.
  onDeleteCode?: (code: string) => void;
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
              {onDeleteCode ? <th>Remove</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.code} className={r.slot_expired ? "gift-row-expired" : undefined}>
                <td>
                  <CopyableCode code={r.code} />
                </td>
                <td>{r.expires}</td>
                <td>{r.slot_expired ? "yes" : "no"}</td>
                <td>{r.needs_run ? "yes" : "no"}</td>
                <td>{r.api_err}</td>
                {playerIds.map((pid) => {
                  const p = r.players[pid];
                  const st = p?.status ?? "—";
                  const cls = STATUS_CLASS[st] ?? "pill-offline";
                  const help = STATUS_HELP[st];
                  const tip = [help, p?.label].filter(Boolean).join(" — ") || undefined;
                  const shortLabel = STATUS_SHORT[st] ?? st;
                  return (
                    <td key={pid}>
                      <span className={`status-pill whitespace-nowrap ${cls}`} title={tip}>
                        {shortLabel}
                      </span>
                    </td>
                  );
                })}
                {onDeleteCode ? (
                  <td>
                    <button
                      type="button"
                      className="btn-secondary px-2 py-1 text-xs"
                      title={`Remove ${r.code}`}
                      onClick={() => onDeleteCode(r.code)}
                    >
                      Remove
                    </button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
