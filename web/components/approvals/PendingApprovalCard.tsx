import Link from "next/link";
import { CopyButton } from "@/components/CopyButton";
import { editDslHref, overlayTestHref } from "@/lib/debug-links";
import type { BusyAction, Decision } from "@/lib/approvals/types";
import type { ClickApprovalView } from "@/lib/types";
import { NavigationRoute } from "./NavigationRoute";
import { TaskContextCaption } from "./TaskContextCaption";

export function PendingApprovalCard({
  view,
  instanceId,
  scenarioLabel,
  regionLabel,
  traceId,
  tempoTraceUrl,
  labelingHref,
  navigation,
  taskContext,
  actionType,
  setNodeTarget,
  payloadJson,
  showPayload,
  onTogglePayload,
  busyAction,
  onDecision,
}: {
  view: ClickApprovalView;
  instanceId: string;
  scenarioLabel: string;
  regionLabel: string;
  traceId: string;
  tempoTraceUrl: string;
  labelingHref: string;
  navigation: ClickApprovalView["navigation"];
  taskContext: ClickApprovalView["task_context"];
  actionType: string;
  setNodeTarget: string;
  payloadJson: string;
  showPayload: boolean;
  onTogglePayload: () => void;
  busyAction: BusyAction;
  onDecision: (d: Decision) => void;
}) {
  const actionLabel = view.action_label || actionType || "action";
  const isBusy = (d: Decision) => busyAction === d;
  const isDisabled = () => busyAction !== null;

  return (
    <>
      {/* Decision row at the TOP of the card so it's always visible
          without scrolling past the scenario blurb. */}
      <div className="actions actions--prominent" role="group" aria-label="Decision">
        <button
          type="button"
          className="btn-approve"
          disabled={isDisabled()}
          onClick={() => onDecision("approve")}
          aria-keyshortcuts="A Y"
        >
          {isBusy("approve") ? "Approving…" : "Approve"}
          <span className="btn-kbd" aria-hidden>A</span>
        </button>
        <button
          type="button"
          className="btn-skip"
          disabled={isDisabled()}
          onClick={() => onDecision("skip")}
          title="Treat as no-op success (don't tap, but don't abort the scenario)"
          aria-keyshortcuts="S"
        >
          {isBusy("skip") ? "Skipping…" : "Skip"}
          <span className="btn-kbd" aria-hidden>S</span>
        </button>
        <button
          type="button"
          className="btn-reject"
          disabled={isDisabled()}
          onClick={() => onDecision("reject")}
          aria-keyshortcuts="R N"
        >
          {isBusy("reject") ? "Rejecting…" : "Reject"}
          <span className="btn-kbd" aria-hidden>R</span>
        </button>
      </div>

      {scenarioLabel ? (
        <div className="scenario-card">
          <strong>{scenarioLabel}</strong>
          {view.scenario_key && view.scenario_key !== scenarioLabel ? (
            <span className="meta">
              <code>{view.scenario_key}</code>
            </span>
          ) : null}
          {view.scenario_key ? (
            <nav className="queue-task-actions approvals-scenario-links" aria-label="Scenario">
              <Link href={editDslHref({ scenario: view.scenario_key })} className="queue-task-actions__link">
                Edit scenario
              </Link>
            </nav>
          ) : null}
        </div>
      ) : null}

      {navigation ? <NavigationRoute info={navigation} /> : null}

      {actionType === "set_node" && setNodeTarget ? (
        <p className="approvals-callout approvals-callout--info">
          Will set <strong>current_screen</strong> to <code>{setNodeTarget}</code>.
        </p>
      ) : null}

      {actionType === "restart_application" ? (
        <p className="approvals-callout approvals-callout--warn">
          Will force-stop and relaunch the game app on this instance.
        </p>
      ) : null}

      {actionType === "ensure_game_foreground" ? (
        <p className="approvals-callout approvals-callout--warn">
          Will launch or bring the game app to the foreground on this instance.
        </p>
      ) : null}

      {actionType === "system_back" ? (
        <p className="approvals-callout approvals-callout--warn">
          Will press Android system Back on this instance.
        </p>
      ) : null}

      {actionType === "diagnostic" ? (
        <>
          <p className="approvals-callout approvals-callout--info">
            {view.diagnostic_kind === "while_match_no_iterations" ? (
              <>
                <code>while_match</code> matched zero times. Approve retries later; reject stops.
              </>
            ) : (
              <>
                Diagnostic check
                {view.diagnostic_kind ? (
                  <>
                    {" "}
                    · <code>{view.diagnostic_kind}</code>
                  </>
                ) : null}
                . Approve retries, reject aborts.
              </>
            )}
          </p>
          {regionLabel && actionType === "diagnostic" ? (
            <p className="meta">
              Region under inspection: <code>{regionLabel}</code>
            </p>
          ) : null}
          {view.diagnostic_attempts ? (
            <p className="meta">
              Initial probes <code>{view.diagnostic_attempts}</code>
              {view.diagnostic_interval ? (
                <>
                  {" "}
                  · interval <code>{view.diagnostic_interval}s</code>
                </>
              ) : null}
            </p>
          ) : null}
        </>
      ) : null}

      {regionLabel && actionType !== "diagnostic" ? (
        <p className="meta">
          Target region: <code>{regionLabel}</code>
        </p>
      ) : null}

      {regionLabel && labelingHref ? (
        <p className="approvals-region-links">
          <Link href={labelingHref} className="queue-task-actions__link">
            Open Labeling for <code>{regionLabel}</code>
          </Link>
          {instanceId ? (
            <Link
              href={overlayTestHref(instanceId, { region: regionLabel })}
              className="queue-task-actions__link"
            >
              Overlay test
            </Link>
          ) : null}
        </p>
      ) : null}

      {taskContext ? <TaskContextCaption ctx={taskContext} /> : null}

      {traceId ? (
        <div className="approvals-trace">
          <span className="meta">Trace ID (Grafana / Tempo trace search)</span>
          <code className="approvals-trace__id">{traceId}</code>
          <CopyButton
            text={traceId}
            label="Copy"
            title="Copy trace ID (paste into Grafana / Tempo)"
            className="approvals-trace__copy"
          />
          {tempoTraceUrl ? (
            <a
              href={tempoTraceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="queue-task-actions__link"
            >
              Open in Tempo
            </a>
          ) : null}
        </div>
      ) : null}

      <details
        className="approvals-payload"
        open={showPayload}
        onToggle={(e) => {
          // Mirror UI state so other interactions can read whether the
          // operator has expanded the raw payload.
          const t = e.currentTarget as HTMLDetailsElement;
          if (t.open !== showPayload) onTogglePayload();
        }}
      >
        <summary className="approvals-payload__summary">
          <span>Payload · {actionLabel}</span>
          <span
            className="approvals-payload__summary-actions"
            onPointerDown={(e) => e.preventDefault()}
            onClick={(e) => e.stopPropagation()}
          >
            <CopyButton
              text={payloadJson}
              label="Copy"
              title="Copy payload JSON"
              className="approvals-payload__copy"
            />
          </span>
        </summary>
        <pre className="code-block">{payloadJson || "—"}</pre>
      </details>
    </>
  );
}
