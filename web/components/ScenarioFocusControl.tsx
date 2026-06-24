"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  clearInstanceFocus,
  fetchInstanceWorkerStatus,
  setInstanceFocus,
} from "@/lib/api";

/**
 * Generic focus-run control: pin a device to run **only** one scenario, with no
 * scheduler, no other instances and no autonomous overlay/identity work. The
 * base primitive behind point-wise launch — fish-detect, the /run launcher and
 * any "play this scenario" button reuse this. Play when idle; Restart + Stop
 * when the device is focused on this scenario.
 */
export function ScenarioFocusControl({
  instanceId,
  scenarioKey,
  player = "",
  title = "Scenario control",
  description,
}: {
  instanceId: string;
  scenarioKey: string;
  player?: string;
  title?: string;
  description?: string;
}) {
  const [message, setMessage] = useState<string | null>(null);

  const workerQuery = useQuery({
    queryKey: ["instanceWorker", instanceId],
    queryFn: () => fetchInstanceWorkerStatus(instanceId),
    enabled: !!instanceId,
    refetchInterval: 4000,
  });

  // "Focused on THIS scenario" — a worker running a *different* focus (or normal
  // autopilot) should not light up this control as active.
  const focusedHere =
    Boolean(workerQuery.data?.running) &&
    workerQuery.data?.focus_scenario === scenarioKey;
  const otherFocus =
    workerQuery.data?.focus_scenario &&
    workerQuery.data.focus_scenario !== scenarioKey
      ? workerQuery.data.focus_scenario
      : "";

  const startMutation = useMutation({
    mutationFn: async (action: "start" | "restart" = "start") => {
      const selected = instanceId.trim();
      if (!selected) throw new Error("Select an instance first.");
      setMessage(
        focusedHere ? "Re-running…" : "Starting focused run…",
      );
      return setInstanceFocus(selected, {
        scenario_key: scenarioKey,
        player_id: player || undefined,
        abort_running: action === "restart",
      });
    },
    onSuccess: (res, action) => {
      void workerQuery.refetch();
      setMessage(
        `${scenarioKey} ${action === "restart" ? "restarted" : "started"} (${res.task_id}).`,
      );
    },
    onError: (e) => setMessage(e instanceof Error ? e.message : String(e)),
  });

  const stopMutation = useMutation({
    mutationFn: () => clearInstanceFocus(instanceId),
    onSuccess: () => {
      void workerQuery.refetch();
      setMessage("Focus cleared, worker stopped.");
    },
    onError: (e) => setMessage(e instanceof Error ? e.message : String(e)),
  });

  const busy = startMutation.isPending || stopMutation.isPending;
  const muted: React.CSSProperties = { fontSize: "0.8rem", opacity: 0.7 };

  return (
    <div className="panel">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "0.5rem",
        }}
      >
        <h3 style={{ margin: 0 }}>{title}</h3>
        <span
          style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem", ...muted }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: focusedHere ? "#22c55e" : "#64748b",
            }}
          />
          {focusedHere ? "focused (isolated)" : "stopped"}
        </span>
      </div>

      <p style={{ marginTop: "0.25rem", ...muted }}>
        {description ?? (
          <>
            Runs <strong>only</strong> <code>{scenarioKey}</code> on this device —
            no scheduler, no other instances, no autonomous work.
          </>
        )}
      </p>
      {otherFocus ? (
        <p style={{ ...muted, color: "#f59e0b" }}>
          This device is focused on <code>{otherFocus}</code>. Starting here will
          switch its focus.
        </p>
      ) : null}

      <div
        className="toolbar"
        style={{ flexWrap: "wrap", alignItems: "center", gap: "0.6rem", marginTop: "0.5rem" }}
      >
        {!focusedHere ? (
          <button
            type="button"
            className="btn-primary"
            disabled={startMutation.isPending || !instanceId}
            onClick={() => startMutation.mutate("start")}
            title={
              instanceId
                ? "Start an isolated worker and run only this scenario"
                : "Select an instance first"
            }
          >
            {startMutation.isPending ? "Starting…" : "▶ Play"}
          </button>
        ) : (
          <>
            <span className="status-pill status-idle" title="Focused run in progress">
              Running
            </span>
            <button
              type="button"
              className="btn-primary"
              disabled={busy || !instanceId}
              onClick={() => startMutation.mutate("restart")}
              title="Abort the running task and start a fresh focused run"
            >
              {startMutation.isPending ? "Restarting…" : "Restart"}
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => stopMutation.mutate()}
              title="Clear focus and stop this device's isolated worker"
            >
              {stopMutation.isPending ? "Stopping…" : "Stop"}
            </button>
          </>
        )}
        {message ? <span style={muted}>{message}</span> : null}
      </div>
    </div>
  );
}
