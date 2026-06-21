"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { AppListbox } from "@/components/headless";
import { useInterval } from "@/lib/useInterval";
import { useFleet } from "@/components/FleetContextProvider";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { instanceCommandSuccessMessage } from "@/lib/instance-command-feedback";
import { CurrentTaskMeta } from "@/components/instance/CurrentTaskMeta";
import { InstanceScenarioHistory } from "@/components/instance/InstanceScenarioHistory";
import { ApprovalCanvas } from "@/components/ApprovalCanvas";
import { StatusPill } from "@/components/ui/StatusPill";
import { playerStateHref } from "@/lib/fleet-links";
import {
  fetchInstanceDetail,
  instancePreviewUrl,
  postAbortTask,
  postInstanceCommand,
} from "@/lib/api";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
import type { InstanceDetail } from "@/lib/types";
import Link from "next/link";

function InstancePageInner() {
  const { instanceId, instancesError } = useFleet();
  const { showSuccess } = useFeedback();
  const [detail, setDetail] = useState<InstanceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [taskType, setTaskType] = useState("");
  const [taskPlayer, setTaskPlayer] = useState("");
  const [switchPlayer, setSwitchPlayer] = useState("");
  // The task target is almost always the account selected above — only show
  // the separate Player selector when the operator explicitly wants to differ.
  const [taskPlayerOverride, setTaskPlayerOverride] = useState(false);
  const [busy, setBusy] = useState(false);
  const [previewKey, setPreviewKey] = useState(() => Date.now());
  const taskTypeRef = useRef(taskType);
  const taskPlayerRef = useRef(taskPlayer);
  taskTypeRef.current = taskType;
  taskPlayerRef.current = taskPlayer;
  const revisionRef = useRef<string | undefined>(undefined);

  const refresh = useCallback(async () => {
    if (!instanceId) return;
    try {
      const result = await fetchInstanceDetail(instanceId, {
        ifRevision: revisionRef.current,
      });
      if ("unchanged" in result) {
        setError(null);
        return;
      }
      revisionRef.current = result.revision;
      setDetail(result);
      if (!taskTypeRef.current && result.runnable_scenarios.length) {
        setTaskType(result.runnable_scenarios[0]);
      }
      if (!taskPlayerRef.current && result.player_ids.length) {
        setTaskPlayer(result.player_ids[0]);
        setSwitchPlayer(result.player_ids[0]);
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [instanceId]);

  useEffect(() => {
    revisionRef.current = undefined;
  }, [instanceId]);

  // Roll the preview cache-buster every second so the <img> URL stays in step
  // with the worker's rolling PNG write cadence — nothing publishes a dashboard
  // event when the file is rewritten, so polling here is the lightest fix.
  useInterval(() => setPreviewKey(Date.now()), instanceId ? 1000 : null);

  useDashboardEventStream({
    topics: ["instance", "queue"],
    instanceId: instanceId || undefined,
    enabled: Boolean(instanceId),
    onEvent: () => {
      void refresh();
    },
    onFallbackPoll: refresh,
  });

  const runCmd = async (body: Parameters<typeof postInstanceCommand>[1]) => {
    if (!instanceId || busy) return;
    setBusy(true);
    try {
      await postInstanceCommand(instanceId, body);
      showSuccess(instanceCommandSuccessMessage(body));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const skipTask = async (restart: boolean) => {
    if (!instanceId || busy) return;
    setBusy(true);
    try {
      await postAbortTask(instanceId, { restart });
      showSuccess(
        restart ? "Task skipped, game restart queued" : "Task skipped",
      );
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const effectiveTaskPlayer = taskPlayerOverride ? taskPlayer : switchPlayer;

  const previewUrl =
    detail?.preview_available && instanceId
      ? instancePreviewUrl(instanceId, previewKey)
      : null;

  const bannerError = error ?? instancesError;

  return (
    <>
      <PageHeader title="Instance" fleet={{ hideQuickLinks: true }}>
        {detail ? <StatusPill status={detail.status} /> : null}
      </PageHeader>
      <ErrorBanner message={bannerError} />
      {detail?.nav_error ? (
        <ErrorBanner message={`Nav error: ${detail.nav_error}`} />
      ) : null}

      <div className="toolbar">
        {detail ? (
          <>
            <span className="meta">Queue: {detail.queue_size}</span>
            <span className="meta">Node: {detail.node}</span>
            <CurrentTaskMeta
              detail={detail}
              busy={busy}
              onSkip={() => void skipTask(false)}
              onSkipAndRestart={() => void skipTask(true)}
            />
            {detail.active_player ? (
              <Link
                className="btn-secondary"
                href={playerStateHref(detail.active_player, { instanceId })}
              >
                Player state
              </Link>
            ) : null}
          </>
        ) : null}
      </div>

      <div className="instance-grid">
        <section className="panel">
          <h2>Manual controls</h2>
          <h3 className="meta">Switch account</h3>
          <div className="toolbar">
            <AppListbox
              inline
              label="Account"
              value={switchPlayer}
              onChange={setSwitchPlayer}
              disabled={!detail?.player_ids.length}
              options={(detail?.player_ids ?? []).map((p) => ({
                value: p,
                label: p,
              }))}
              minWidth={200}
            />
            <button
              type="button"
              className="btn-secondary"
              disabled={busy || !switchPlayer}
              onClick={() =>
                runCmd({ cmd: "switch_player", player_id: switchPlayer })
              }
            >
              Queue switch
            </button>
          </div>

          <h3 className="meta">Run task</h3>
          <div className="toolbar">
            <AppListbox
              inline
              label="Task"
              value={taskType}
              onChange={setTaskType}
              options={(detail?.runnable_scenarios ?? []).map((s) => ({
                value: s,
                label: s,
              }))}
              minWidth={220}
            />
            {taskPlayerOverride ? (
              <AppListbox
                inline
                label="Player"
                value={taskPlayer}
                onChange={setTaskPlayer}
                options={(detail?.player_ids ?? []).map((p) => ({
                  value: p,
                  label: p,
                }))}
                minWidth={200}
              />
            ) : null}
            <button
              type="button"
              className="btn-secondary"
              disabled={busy || !taskType || !effectiveTaskPlayer}
              title={
                effectiveTaskPlayer ? `Runs for ${effectiveTaskPlayer}` : undefined
              }
              onClick={() =>
                runCmd({
                  cmd: "run_task",
                  task_type: taskType,
                  player_id: effectiveTaskPlayer,
                })
              }
            >
              Queue task
            </button>
            <button
              type="button"
              className="meta cursor-pointer bg-transparent p-0 underline decoration-dotted underline-offset-2 hover:text-wos-text"
              onClick={() => {
                // Seed the override selector with the player the task would
                // have targeted, so opening Advanced changes nothing by itself.
                if (!taskPlayerOverride && switchPlayer) {
                  setTaskPlayer(switchPlayer);
                }
                setTaskPlayerOverride(!taskPlayerOverride);
              }}
            >
              {taskPlayerOverride
                ? "Use selected account"
                : "Advanced: run for another player…"}
            </button>
          </div>
          {!taskPlayerOverride && effectiveTaskPlayer ? (
            <p className="meta">
              Runs for the selected account: <code>{effectiveTaskPlayer}</code>
            </p>
          ) : null}

          <h3 className="meta">Restart game</h3>
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => runCmd({ cmd: "restart" })}
          >
            Restart game
          </button>

        </section>

        <section className="panel">
          <h2>Preview</h2>
          {previewUrl ? (
            <ApprovalCanvas
              imageUrl={previewUrl}
              width={720}
              height={1280}
              overlays={[]}
            />
          ) : (
            <p className="meta">
              No preview yet — start the bot worker so it publishes frames.
            </p>
          )}
        </section>
      </div>

      {detail && detail.history.length > 0 && instanceId ? (
        <section className="panel instance-history-panel">
          <InstanceScenarioHistory rows={detail.history} instanceId={instanceId} />
        </section>
      ) : null}
    </>
  );
}

export default function InstancePage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <InstancePageInner />
    </Suspense>
  );
}
