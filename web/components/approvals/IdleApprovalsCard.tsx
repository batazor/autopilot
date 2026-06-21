import { Button } from "@/components/ui";

export function IdleApprovalsCard({
  busy,
  resetting,
  onResetScreen,
  instanceId,
  stalePending,
  staleWorkerTask,
  staleTaskLabel,
  clearingPending,
  onClearPending,
}: {
  busy: boolean;
  resetting: boolean;
  onResetScreen: () => void;
  instanceId: string;
  stalePending: boolean;
  staleWorkerTask: boolean;
  staleTaskLabel: string;
  clearingPending: boolean;
  onClearPending: () => void;
}) {
  // A leftover request from a stopped bot: there's no worker to consume an
  // approve/reject, so we don't prompt for a decision. Explain that instead of
  // claiming "All clear", and offer to clear the orphaned request.
  if (stalePending) {
    return (
      <div className="idle-card">
        <div className="idle-card__icon" aria-hidden>⏸</div>
        <p className="idle-card__title">Bot not running</p>
        <p className="meta">
          A click request is parked for this instance, but the worker isn&apos;t
          alive to act on a decision — so there&apos;s nothing to approve right
          now. Start the bot and it will re-issue any request it still needs.
        </p>
        <Button
          className="mt-3"
          disabled={busy || !instanceId}
          onClick={onClearPending}
          title="Drop the orphaned pending approval from Redis (treated as reject)"
        >
          {clearingPending ? "Clearing…" : "Clear parked request"}
        </Button>
      </div>
    );
  }
  if (staleWorkerTask) {
    return (
      <div className="idle-card">
        <div className="idle-card__icon" aria-hidden>⏸</div>
        <p className="idle-card__title">Bot not running</p>
        <p className="meta">
          Redis still shows {staleTaskLabel || "a task"} as running, but this
          instance has no fresh worker heartbeat. Start the bot; boot cleanup
          will close the orphaned task and fresh work can be queued.
        </p>
      </div>
    );
  }
  return (
    <div className="idle-card">
      <div className="idle-card__icon" aria-hidden>✓</div>
      <p className="idle-card__title">All clear</p>
      <p className="meta">
        No pending click requests for this instance. The worker will queue a
        decision here as soon as it needs one.
      </p>
      <Button
        className="mt-3"
        disabled={busy || !instanceId}
        onClick={onResetScreen}
        title="Clears current_screen in Redis (useful when the worker is stuck on the wrong node)"
      >
        {resetting ? "Resetting…" : "Reset node to none (unknown)"}
      </Button>
    </div>
  );
}
