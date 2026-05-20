"use client";

import { Suspense, useCallback, useRef, useState } from "react";
import { AppListbox } from "@/components/headless";
import { useFleet } from "@/components/FleetContextProvider";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { instanceCommandSuccessMessage } from "@/lib/instance-command-feedback";
import { InstanceScenarioHistory } from "@/components/instance/InstanceScenarioHistory";
import { StatusPill } from "@/components/StatusPill";
import { playerStateHref } from "@/lib/fleet-links";
import {
  fetchInstanceDetail,
  instancePreviewUrl,
  postInstanceCommand,
} from "@/lib/api";
import { usePollWhenVisible } from "@/lib/hooks";
import type { InstanceDetail } from "@/lib/types";
import Link from "next/link";

const POLL_MS = 2000;

function InstancePageInner() {
  const { instanceId, instancesError } = useFleet();
  const { showSuccess } = useFeedback();
  const [detail, setDetail] = useState<InstanceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [taskType, setTaskType] = useState("");
  const [taskPlayer, setTaskPlayer] = useState("");
  const [switchPlayer, setSwitchPlayer] = useState("");
  const [busy, setBusy] = useState(false);
  const taskTypeRef = useRef(taskType);
  const taskPlayerRef = useRef(taskPlayer);
  taskTypeRef.current = taskType;
  taskPlayerRef.current = taskPlayer;

  const refresh = useCallback(async () => {
    if (!instanceId) return;
    try {
      const d = await fetchInstanceDetail(instanceId);
      setDetail(d);
      if (!taskTypeRef.current && d.runnable_scenarios.length) {
        setTaskType(d.runnable_scenarios[0]);
      }
      if (!taskPlayerRef.current && d.player_ids.length) {
        setTaskPlayer(d.player_ids[0]);
        setSwitchPlayer(d.player_ids[0]);
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [instanceId]);

  usePollWhenVisible(refresh, POLL_MS);

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

  const previewUrl =
    detail?.preview_available && instanceId
      ? instancePreviewUrl(instanceId, detail.preview_mtime ?? undefined)
      : null;

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
          {previewUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={previewUrl}
              alt="Rolling instance preview"
              style={{ maxWidth: "100%", borderRadius: 6 }}
            />
          ) : (
            <p className="meta">
              No rolling PNG yet — bot worker must capture ADB screenshots.
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
