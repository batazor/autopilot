"use client";

import {
  Dialog,
  DialogBackdrop,
  DialogPanel,
  DialogTitle,
} from "@headlessui/react";
import { useCallback, useState } from "react";
import { Button, Icon, Pill, type PillTone, Spinner } from "@/components/ui";
import {
  type DailyTasksView,
  type DailyTaskView,
  fetchDailyTasks,
} from "@/lib/api";

// Human labels for the mission categories the quest reader emits.
const CATEGORY_LABEL: Record<string, string> = {
  build: "Building",
  research: "Research",
  train: "Train troops",
  gather: "Gather",
  stamina: "Intel / stamina",
  heal: "Heal",
  help: "Alliance",
  arena: "Arena",
  event: "Event",
};

function taskLabel(t: DailyTaskView): string {
  const base = CATEGORY_LABEL[t.category] ?? t.category;
  const suffix = t.id.includes(":") ? t.id.split(":")[1] : "";
  return suffix ? `${base} · ${suffix}` : base;
}

function formatReset(seconds: number | null): string | null {
  if (seconds == null) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

type Props = { playerId: string; nickname?: string };

/** A button that opens a panel listing this account's daily missions + status. */
export function DailyTasksButton({ playerId, nickname }: Props) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<DailyTasksView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchDailyTasks(playerId)
      .then(setData)
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : String(e)),
      )
      .finally(() => setLoading(false));
  }, [playerId]);

  const reset = data ? formatReset(data.refresh_in_s) : null;

  return (
    <>
      <Button
        variant="secondary"
        className="inline-flex items-center gap-1 px-2 py-1 text-xs"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
          load();
        }}
        title="Daily tasks"
      >
        <Icon name="calendar" size="sm" />
        Tasks
      </Button>

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        className="headless-dialog-root"
      >
        <DialogBackdrop transition className="headless-dialog__backdrop" />
        <div className="headless-dialog__container">
          <DialogPanel transition className="headless-dialog__panel">
            <DialogTitle className="headless-dialog__title">
              Daily tasks{nickname ? ` · ${nickname}` : ""}
            </DialogTitle>
            <div className="headless-dialog__body">
              {loading && (
                <div className="flex items-center gap-2 muted">
                  <Spinner size="sm" /> Loading…
                </div>
              )}
              {error && (
                <p className="m-0 text-red-300">Failed to load: {error}</p>
              )}
              {!loading && !error && data && (
                <>
                  <div className="mb-3 flex items-center justify-between text-sm muted">
                    <span>
                      {data.summary.done}/{data.summary.total} done
                      {data.summary.claimable > 0
                        ? ` · ${data.summary.claimable} to claim`
                        : ""}
                    </span>
                    {reset && <span>Resets in {reset}</span>}
                  </div>

                  {!data.read && (
                    <p className="m-0 muted">
                      No daily list read for this account yet.
                    </p>
                  )}

                  {data.tasks.length > 0 && (
                    <ul
                      className="m-0 flex flex-col gap-2 p-0"
                      style={{ listStyle: "none" }}
                    >
                      {data.tasks.map((t) => {
                        const pct = Math.min(
                          100,
                          Math.round((t.progress / Math.max(1, t.target)) * 100),
                        );
                        const tone: PillTone = t.done ? "ok" : "neutral";
                        const label = t.done
                          ? t.claimable
                            ? "claim"
                            : "done"
                          : "open";
                        return (
                          <li key={t.id} className="flex items-center gap-3">
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between gap-2">
                                <span className="truncate">{taskLabel(t)}</span>
                                <span className="muted text-xs tabular-nums">
                                  {t.progress}/{t.target}
                                </span>
                              </div>
                              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-wos-surface">
                                <div
                                  className="h-full rounded-full"
                                  style={{
                                    width: `${pct}%`,
                                    background: t.done
                                      ? "var(--wos-status-ok-fg)"
                                      : "var(--wos-accent)",
                                  }}
                                />
                              </div>
                            </div>
                            <Pill tone={tone}>{label}</Pill>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </>
              )}
            </div>
            <div className="headless-dialog__actions">
              <Button
                className="inline-flex items-center gap-1"
                disabled={loading}
                onClick={load}
              >
                <Icon name="refresh" size="sm" /> Refresh
              </Button>
              <Button variant="primary" onClick={() => setOpen(false)}>
                Close
              </Button>
            </div>
          </DialogPanel>
        </div>
      </Dialog>
    </>
  );
}
