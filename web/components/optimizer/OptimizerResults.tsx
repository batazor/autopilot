"use client";

import type { OptimizerSolveResult } from "@/lib/config-pages";

export function OptimizerResults({
  data,
  onDryRun,
  onApprove,
  onQueue,
  gamerId,
  instanceId,
  busy,
}: {
  data: OptimizerSolveResult;
  gamerId?: string;
  instanceId?: string;
  busy?: boolean;
  onDryRun?: () => void;
  onApprove?: () => void;
  onQueue?: () => void;
}) {
  const m = data.metrics;
  const nc = data.next_command;

  return (
    <>
      <div className="metrics-row">
        <div className="metric-card panel">
          <span className="muted">Solver</span>
          <strong>{m.status}</strong>
        </div>
        <div className="metric-card panel">
          <span className="muted">Objective</span>
          <strong>{m.objective.toLocaleString()}</strong>
        </div>
        <div className="metric-card panel">
          <span className="muted">Selected</span>
          <strong>{m.selected_count}</strong>
        </div>
        <div className="metric-card panel">
          <span className="muted">Rejected</span>
          <strong>{m.rejected_count}</strong>
        </div>
        <div className="metric-card panel">
          <span className="muted">Pruned</span>
          <strong>{m.pruned_count}</strong>
        </div>
        <div className="metric-card panel">
          <span className="muted">Profile</span>
          <strong>{m.profile_id || "(none)"}</strong>
        </div>
      </div>
      {m.profile_description && (
        <p className="muted">{m.profile_description}</p>
      )}

      {nc && (
        <section className="panel" style={{ marginTop: "1rem" }}>
          <h2>Next command</h2>
          <p>
            <strong>{nc.headline}</strong>
          </p>
          <p className="muted">
            Dispatch: <code>{nc.dispatch.dsl_scenario}</code> → node{" "}
            <code>{nc.dispatch.set_node}</code>
            {nc.dispatch.region ? (
              <>
                {" "}
                · region <code>{nc.dispatch.region}</code>
              </>
            ) : null}
          </p>
          <ul>
            {nc.reasons.map((r) => (
              <li key={r}>
                <code>{r}</code>
              </li>
            ))}
          </ul>
          {(onDryRun || onApprove || onQueue) && (
            <div className="toolbar">
              {onDryRun && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busy}
                  onClick={onDryRun}
                >
                  Dry run
                </button>
              )}
              {gamerId && onApprove && (
                <button
                  type="button"
                  className="btn-primary"
                  disabled={busy}
                  onClick={onApprove}
                >
                  Record as done
                </button>
              )}
              {gamerId && instanceId && onQueue && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busy || !instanceId}
                  onClick={onQueue}
                >
                  Queue for bot
                </button>
              )}
            </div>
          )}
        </section>
      )}

      <section className="panel" style={{ marginTop: "1rem" }}>
        <h2>Plan</h2>
        <DataTable rows={data.plan} />
      </section>

      <section className="panel" style={{ marginTop: "1rem" }}>
        <h2>Candidates</h2>
        <DataTable rows={data.candidates} />
      </section>

      <section className="panel" style={{ marginTop: "1rem" }}>
        <h2>Resources</h2>
        <DataTable rows={data.resources} />
      </section>
    </>
  );
}

function DataTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (!rows.length) return <p className="muted">No rows.</p>;
  const cols = Object.keys(rows[0]);
  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c}>{String(row[c] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
