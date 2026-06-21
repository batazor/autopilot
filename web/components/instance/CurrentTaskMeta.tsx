"use client";

import { useState } from "react";
import type { InstanceDetail } from "@/lib/types";
import { useInterval } from "@/lib/useInterval";

function formatElapsed(seconds: number): string {
  const sec = Math.max(0, Math.floor(seconds));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * "Task: <name> · <timer>" with a live (client-ticked) elapsed instead of the
 * fetch-time snapshot, turning red past the worker's task timeout — the point
 * where a task is only still running because the timeout is disabled
 * (approval mode) or the state is a zombie. Past it, the skip affordances
 * appear: abort goes over pubsub, so it reaches the worker mid-task.
 */
export function CurrentTaskMeta({
  detail,
  busy,
  onSkip,
  onSkipAndRestart,
}: {
  detail: InstanceDetail;
  busy: boolean;
  onSkip: () => void;
  onSkipAndRestart: () => void;
}) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const running = detail.task_started_at != null;

  useInterval(() => setNow(Date.now() / 1000), running ? 1000 : null);

  if (!running) {
    return <span className="meta">Task: {detail.task}</span>;
  }

  const elapsed = now - (detail.task_started_at ?? now);
  const threshold = detail.task_stuck_after_s;
  // A dead worker leaves the busy state behind; its timer would climb forever
  // while the real problem is "worker down" — only flag live workers.
  const stuck = threshold > 0 && elapsed > threshold && detail.status === "live";

  return (
    <span className="meta inline-flex items-center gap-2">
      <span>
        Task: {detail.task_scenario || detail.task} ·{" "}
        <span
          className={stuck ? "font-semibold text-red-400" : undefined}
          title={
            stuck
              ? `Exceeds the ${Math.round(threshold / 60)}m task timeout`
              : undefined
          }
        >
          {formatElapsed(elapsed)}
        </span>
      </span>
      {stuck ? (
        <>
          <span className="font-medium text-red-400">Stuck?</span>
          <button
            type="button"
            className="btn-secondary"
            disabled={busy}
            onClick={onSkip}
          >
            Skip task
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={busy}
            onClick={onSkipAndRestart}
          >
            Skip &amp; restart game
          </button>
        </>
      ) : null}
    </span>
  );
}
