"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, Card, Chip, Pill } from "@/components/ui";
import { fetchFleetPlan, type FleetPlanRow } from "@/lib/api";

export function FleetPlanView() {
  const [rows, setRows] = useState<FleetPlanRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError(null);
    setBusy(true);
    fetchFleetPlan()
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => load(), [load]);

  return (
    <Card title="Fleet — next action per account">
      <div className="mb-3 flex items-center gap-3">
        <Button variant="secondary" pending={busy} onClick={load}>
          Refresh
        </Button>
        {rows ? (
          <span className="text-sm text-wos-text-muted">{rows.length} accounts</span>
        ) : null}
      </div>

      {error ? <p className="error-banner">{error}</p> : null}

      {rows === null && !error ? (
        <p className="text-sm text-wos-text-muted">Loading…</p>
      ) : null}

      {rows && rows.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-wos-border-subtle">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-wos-panel-raised text-left text-xs uppercase tracking-wide text-wos-text-muted">
                <th className="px-3 py-2 font-medium">Account</th>
                <th className="px-3 py-2 font-medium">Building</th>
                <th className="px-3 py-2 font-medium">Research</th>
                <th className="px-3 py-2 font-medium">Hero</th>
                <th className="px-3 py-2 font-medium">Pet</th>
                <th className="px-3 py-2 font-medium">Channels</th>
                <th className="px-3 py-2 font-medium">Bottleneck</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.player_id}
                  className="border-t border-wos-border-subtle align-top"
                >
                  <td className="px-3 py-2">
                    <div className="font-medium text-wos-text">
                      {r.nickname || r.player_id}
                    </div>
                    {r.nickname ? (
                      <div className="text-xs text-wos-text-muted">{r.player_id}</div>
                    ) : null}
                  </td>
                  {r.error ? (
                    <td className="px-3 py-2 text-wos-text-muted" colSpan={6}>
                      <Pill tone="danger">error</Pill> {r.error}
                    </td>
                  ) : (
                    <>
                      <td className="px-3 py-2 text-wos-text">{r.picks.building ?? "—"}</td>
                      <td className="px-3 py-2 text-wos-text">{r.picks.research ?? "—"}</td>
                      <td className="px-3 py-2 text-wos-text">{r.picks.heroes ?? "—"}</td>
                      <td className="px-3 py-2 text-wos-text">{r.picks.pets ?? "—"}</td>
                      <td className="px-3 py-2 text-wos-text-muted">{r.committed}</td>
                      <td className="px-3 py-2">
                        {r.bottleneck.length ? (
                          <div className="flex flex-wrap gap-1">
                            {r.bottleneck.map((b) => (
                              <Chip key={b}>{b}</Chip>
                            ))}
                          </div>
                        ) : (
                          <span className="text-wos-text-muted">—</span>
                        )}
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {rows && rows.length === 0 ? (
        <p className="text-sm text-wos-text-muted">No players found.</p>
      ) : null}
    </Card>
  );
}
