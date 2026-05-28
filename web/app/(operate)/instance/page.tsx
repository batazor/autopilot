"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { AppListbox } from "@/components/headless";
import { useFleet } from "@/components/FleetContextProvider";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { instanceCommandSuccessMessage } from "@/lib/instance-command-feedback";
import { InstanceScenarioHistory } from "@/components/instance/InstanceScenarioHistory";
import { ApprovalCanvas } from "@/components/ApprovalCanvas";
import { StatusPill } from "@/components/StatusPill";
import { playerStateHref } from "@/lib/fleet-links";
import {
  fetchInstanceDetail,
  h264StreamUrl,
  instancePreviewUrl,
  postInstanceCommand,
} from "@/lib/api";
import { isWebCodecsSupported } from "@/lib/h264VideoStream";
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
  const [busy, setBusy] = useState(false);
  const [previewKey, setPreviewKey] = useState(() => Date.now());
  // When scrcpy is live we render H.264 video; if the stream drops mid-session
  // (worker stopped, scrcpy restart) we flip this off so the rolling PNG takes
  // over without the operator having to refresh the page.
  const [streamClosed, setStreamClosed] = useState(false);
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
    setStreamClosed(false);
  }, [instanceId]);

  const streamAvailable = Boolean(
    detail?.stream?.available && !streamClosed && isWebCodecsSupported(),
  );

  // Roll the preview cache-buster every second so the <img> URL stays in step
  // with the worker's rolling PNG write cadence — nothing publishes a dashboard
  // event when the file is rewritten, so polling here is the lightest fix.
  // Skip while the WebCodecs stream is active: the canvas draws every VideoFrame
  // on its own, and bumping previewKey would just trigger needless renders.
  useEffect(() => {
    if (!instanceId || streamAvailable) return;
    const id = setInterval(() => setPreviewKey(Date.now()), 1000);
    return () => clearInterval(id);
  }, [instanceId, streamAvailable]);

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

  const streamUrl = streamAvailable && instanceId ? h264StreamUrl(instanceId) : null;
  const previewUrl =
    !streamUrl && detail?.preview_available && instanceId
      ? instancePreviewUrl(instanceId, previewKey)
      : null;
  const handleStreamClosed = useCallback(() => {
    // Server closed the WebSocket (scrcpy restart, worker stop, browser
    // tab throttled). Stay on the rolling PNG for the rest of this page
    // load — reopening here would loop if the stream is genuinely broken.
    // The operator can refresh or switch instances to retry.
    setStreamClosed(true);
  }, []);

  const bannerError = error ?? instancesError;

  return (
    <>
      <FleetPageHeader title="Instance" hideQuickLinks>
        {detail ? <StatusPill status={detail.status} /> : null}
      </FleetPageHeader>
      <ErrorBanner message={bannerError} />
      {detail?.nav_error ? (
        <ErrorBanner message={`Nav error: ${detail.nav_error}`} />
      ) : null}

      <div className="toolbar">
        {detail ? (
          <>
            <span className="meta">Queue: {detail.queue_size}</span>
            <span className="meta">Node: {detail.node}</span>
            <span className="meta">Task: {detail.task}</span>
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
            <button
              type="button"
              className="btn-secondary"
              disabled={busy || !taskType || !taskPlayer}
              onClick={() =>
                runCmd({
                  cmd: "run_task",
                  task_type: taskType,
                  player_id: taskPlayer,
                })
              }
            >
              Queue task
            </button>
          </div>

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
          {streamUrl || previewUrl ? (
            <ApprovalCanvas
              streamUrl={streamUrl}
              imageUrl={previewUrl}
              width={720}
              height={1280}
              overlays={[]}
              onStreamClosed={handleStreamClosed}
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
