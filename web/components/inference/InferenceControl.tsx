"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import {
  fetchInferenceLogs,
  fetchInferenceStatus,
  startInference,
  stopInference,
} from "@/lib/api";
import { Icon } from "@/components/ui/Icon";
import { useApiOffline } from "@/components/ApiStatusProvider";
import type { InferencePhase, InferenceStatusView } from "@/lib/types";

const STATUS_KEY = ["inferenceStatus"] as const;
const LOGS_KEY = ["inferenceLogs"] as const;

// Poll fast while the container is moving between phases, slow once it settles.
const POLL_FAST_MS = 2000;
const POLL_IDLE_MS = 6000;

const PHASE_META: Record<InferencePhase, { label: string; pill: string }> = {
  docker_unavailable: { label: "Docker unavailable", pill: "pill-stale" },
  not_installed: { label: "Not installed", pill: "pill-stale" },
  pulling: { label: "Pulling image…", pill: "pill-busy" },
  starting: { label: "Starting…", pill: "pill-busy" },
  unhealthy: { label: "Unhealthy", pill: "pill-danger" },
  ready: { label: "Ready", pill: "pill-live" },
  stopped: { label: "Stopped", pill: "pill-paused" },
  error: { label: "Error", pill: "pill-danger" },
};

// The four user-facing stages: install → download → start → ready.
const STEPS = [
  { label: "Install", hint: "image" },
  { label: "Download", hint: "docker pull" },
  { label: "Start", hint: "container" },
  { label: "Ready", hint: "detector responding" },
] as const;

type StepState = "done" | "active" | "error" | "pending";

function phaseOrder(phase: InferencePhase): number {
  switch (phase) {
    case "not_installed":
      return 0;
    case "pulling":
      return 1;
    case "starting":
    case "unhealthy":
      return 2;
    case "ready":
      return 3;
    default:
      return -1;
  }
}

function stepState(i: number, s: InferenceStatusView): StepState {
  if (s.phase === "ready") return "done";
  if (s.phase === "stopped") {
    return i === 0 && s.image_present ? "done" : "pending";
  }
  if (s.phase === "error") {
    const errIdx = s.image_present ? 2 : 1;
    if (i < errIdx) return "done";
    if (i === errIdx) return "error";
    return "pending";
  }
  const order = phaseOrder(s.phase);
  if (order < 0) return "pending"; // docker_unavailable
  if (i < order) return "done";
  if (i === order) return s.phase === "unhealthy" ? "error" : "active";
  return "pending";
}

function isTransitional(phase: InferencePhase): boolean {
  return phase === "pulling" || phase === "starting";
}

export function InferenceControl() {
  const qc = useQueryClient();
  // When the whole API is down, the global "API offline" indicator already says
  // so — don't repeat it as a status-fetch banner here (one place is enough).
  const apiOffline = useApiOffline();
  const [showLogs, setShowLogs] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement | null>(null);

  const statusQuery = useQuery<InferenceStatusView>({
    queryKey: STATUS_KEY,
    queryFn: fetchInferenceStatus,
    refetchInterval: (query) => {
      const s = query.state.data;
      if (!s) return POLL_FAST_MS;
      return isTransitional(s.phase) || s.job_active ? POLL_FAST_MS : POLL_IDLE_MS;
    },
  });

  const status = statusQuery.data ?? null;

  const logsQuery = useQuery({
    queryKey: LOGS_KEY,
    queryFn: () => fetchInferenceLogs(300),
    enabled: showLogs,
    refetchInterval: showLogs ? POLL_FAST_MS : false,
  });

  // Keep the log window pinned to the newest line.
  useEffect(() => {
    if (showLogs && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logsQuery.data, showLogs]);

  const applyResult = (data: InferenceStatusView) => {
    qc.setQueryData(STATUS_KEY, data);
    setLocalError(null);
  };

  const startMutation = useMutation({
    mutationFn: startInference,
    onSuccess: applyResult,
    onError: (e) =>
      setLocalError(e instanceof Error ? e.message : "Failed to start"),
  });
  const stopMutation = useMutation({
    mutationFn: stopInference,
    onSuccess: applyResult,
    onError: (e) =>
      setLocalError(e instanceof Error ? e.message : "Failed to stop"),
  });

  const queryError =
    statusQuery.isError && statusQuery.error instanceof Error
      ? statusQuery.error.message
      : null;
  const phaseError = status?.error?.trim() ? status.error : null;
  const error = localError ?? queryError ?? phaseError;

  const phase = status?.phase ?? "docker_unavailable";
  const meta = PHASE_META[phase];
  const dockerOk = phase !== "docker_unavailable";
  const running = status?.container_status === "running";
  const pulling = phase === "pulling";
  const canStop = running || phase === "starting" || phase === "unhealthy";
  const busy = startMutation.isPending || stopMutation.isPending;
  const startLabel = phase === "not_installed" ? "Install & start" : "Start";

  return (
    <section className="panel infctl">
      <div className="infctl__head">
        <div className="infctl__title">
          <Icon name="modules" size="sm" />
          <h2>Inference service</h2>
          {status ? (
            <span className={meta.pill} title={`phase: ${phase}`}>
              {meta.label}
            </span>
          ) : (
            <span className="pill-stale">Checking…</span>
          )}
        </div>
        <div className="infctl__actions">
          {canStop ? (
            <button
              type="button"
              className="btn-secondary"
              disabled={!dockerOk || busy}
              onClick={() => stopMutation.mutate()}
              title="Stop the container (a restart is instant)"
            >
              <Icon name="stop" size="sm" /> Stop
            </button>
          ) : (
            <button
              type="button"
              className="btn-primary"
              disabled={!dockerOk || busy || pulling}
              onClick={() => startMutation.mutate()}
              title={
                dockerOk
                  ? "Pull the image if needed and bring the container up"
                  : "Docker is not reachable from the API process"
              }
            >
              <Icon name="play" size="sm" /> {busy ? "Starting…" : startLabel}
            </button>
          )}
          <button
            type="button"
            className="btn-secondary"
            onClick={() => statusQuery.refetch()}
            disabled={statusQuery.isFetching}
            title="Refresh status"
            aria-label="Refresh status"
          >
            <Icon name="refresh" size="sm" />
          </button>
        </div>
      </div>

      {/* Stepper: install → download → start → ready */}
      <ol className="infctl-steps" aria-label="Startup stages">
        {STEPS.map((step, i) => {
          const st = status ? stepState(i, status) : "pending";
          return (
            <li key={step.label} className={`infctl-step infctl-step--${st}`}>
              <span className="infctl-step__dot" aria-hidden>
                {st === "done" ? (
                  <Icon name="check" size="sm" />
                ) : st === "error" ? (
                  <Icon name="warning" size="sm" />
                ) : (
                  <span className="infctl-step__num">{i + 1}</span>
                )}
              </span>
              <span className="infctl-step__label">{step.label}</span>
              <span className="infctl-step__hint">{step.hint}</span>
            </li>
          );
        })}
      </ol>

      {/* Liveness / endpoint meta */}
      <div className="infctl-meta">
        <span title="Container state">
          container: <code>{status?.container_status || "—"}</code>
        </span>
        <span title="Healthcheck">
          health: <code>{status?.health || "—"}</code>
        </span>
        {status?.url ? (
          <span title="Detector endpoint">
            url: <code>{status.url}</code>
          </span>
        ) : null}
        {status?.model_id ? (
          <span>
            model: <code>{status.model_id}</code>
          </span>
        ) : null}
        <button
          type="button"
          className="infctl-logs-toggle"
          onClick={() => setShowLogs((v) => !v)}
        >
          {showLogs ? "Hide logs" : "Logs"}
        </button>
      </div>

      {error && !apiOffline ? (
        <div className="error-banner">{error}</div>
      ) : null}

      {status && phase === "docker_unavailable" ? (
        <p className="infctl-note">
          The API process can&apos;t reach Docker. Run the dashboard where{" "}
          <code>docker</code> is available locally; in production mount{" "}
          <code>/var/run/docker.sock</code> into the <code>api</code> container.
        </p>
      ) : null}

      {showLogs ? (
        <pre className="infctl-logs" ref={logRef}>
          {logsQuery.data?.lines?.length
            ? logsQuery.data.lines.join("\n")
            : logsQuery.isLoading
              ? "Loading logs…"
              : "No logs yet — press Start."}
        </pre>
      ) : null}
    </section>
  );
}
