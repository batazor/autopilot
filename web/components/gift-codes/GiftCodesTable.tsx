import type { GiftCodeRow } from "@/lib/wiki";
import { STATUS_CLASS, STATUS_HELP, STATUS_SHORT } from "@/lib/gift-codes/types";
import { CopyableCode } from "./CopyableCode";

export function GiftCodesTable({
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
