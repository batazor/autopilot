"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useFleet } from "@/components/FleetContextProvider";
import { AppListbox } from "@/components/headless";
import {
  clickApprovalImageUrl,
  fetchOverlayTest,
  fetchRegionOcr,
} from "@/lib/api";
import {
  DREAMSCAPE_WORD_REGIONS,
  DREAMSCAPE_WORDS_REF,
  deriveLiveStatus,
  wordBadges,
} from "@/lib/dreamscape-live";
import type { WordBadge } from "@/lib/dreamscape-live";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
import { LiveStatusCard } from "./LiveStatusCard";

const POLL_MS = 1500;

/** The live status view is shared between solo (3 words) and multiplayer (6
 * words); the word-region set and reference screen are the only differences. */
export type LiveEditorTabProps = {
  /** OCR word-button regions to poll/show as badges (defaults to solo's 3). */
  wordRegions?: readonly string[];
  /** Reference screen this mode keys its OCR poll on (defaults to solo's). */
  wordsRef?: string;
};

export function LiveEditorTab({
  wordRegions = DREAMSCAPE_WORD_REGIONS,
  wordsRef = DREAMSCAPE_WORDS_REF,
}: LiveEditorTabProps = {}) {
  const { instanceId, instances, setInstanceId, instancesLoading } = useFleet();

  // ── Live polling (status + detected words) ──
  const overlayQuery = useQuery({
    queryKey: ["dreamscape-overlay", instanceId],
    queryFn: () => fetchOverlayTest(instanceId),
    enabled: Boolean(instanceId),
    refetchInterval: POLL_MS,
  });
  const ocrQuery = useQuery({
    queryKey: ["dreamscape-ocr", instanceId, wordsRef],
    queryFn: () => fetchRegionOcr(instanceId, [...wordRegions]),
    enabled: Boolean(instanceId),
    refetchInterval: POLL_MS,
  });

  const status = useMemo(
    () => deriveLiveStatus(overlayQuery.data),
    [overlayQuery.data],
  );
  const badges = useMemo(
    () => wordBadges(ocrQuery.data?.rows, wordRegions),
    [ocrQuery.data, wordRegions],
  );

  // Live device frame, 1:1 with the approvals page: the worker's rolling
  // preview PNG, refreshed the instant the instance revision advances (SSE
  // below) by bumping a cache-busting tick.
  const [imageTick, setImageTick] = useState(0);
  const cardImageUrl = instanceId
    ? `${clickApprovalImageUrl(instanceId, "live")}&tick=${imageTick}`
    : null;

  // Keep the frame continuously current like the approvals screen: the worker
  // bumps the ``instance`` revision whenever it writes a new rolling preview;
  // a fallback poll covers degraded/closed SSE streams.
  useDashboardEventStream({
    topics: ["instance"],
    instanceId: instanceId || undefined,
    enabled: Boolean(instanceId),
    onEvent: (topic) => {
      if (topic === "instance") setImageTick((t) => t + 1);
    },
    onFallbackPoll: () => setImageTick((t) => t + 1),
  });

  const instanceOptions = instances.map((id) => ({ value: id, label: id }));

  return (
    <div className="mt-4 space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <AppListbox
          label="Instance"
          options={instanceOptions}
          value={instanceId}
          onChange={setInstanceId}
          loading={instancesLoading}
          placeholder="Select a device"
          inline
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(260px,340px)_1fr]">
        <LiveStatusCard
          imageUrl={cardImageUrl}
          status={status}
          badges={badges}
          loading={ocrQuery.isFetching}
          instanceSelected={Boolean(instanceId)}
          showWords={false}
        />
        <WordSearchPanel
          badges={badges}
          loading={ocrQuery.isFetching}
          instanceSelected={Boolean(instanceId)}
        />
      </div>
    </div>
  );
}

/** Right-hand panel: the ordered words the bot is currently reading from the
 * level, one row each, with the detected text and per-word OCR confidence. */
function WordSearchPanel({
  badges,
  loading,
  instanceSelected,
}: {
  badges: WordBadge[];
  loading: boolean;
  instanceSelected: boolean;
}) {
  return (
    <section className="panel">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">
          Words to find{" "}
          <span className="text-sm font-normal text-wos-text-muted">
            ({badges.length})
          </span>
        </h2>
        {loading ? <span className="meta">refreshing…</span> : null}
      </div>

      {!instanceSelected ? (
        <p className="meta">Select an instance to read the level&apos;s words.</p>
      ) : (
        <ol className="space-y-2">
          {badges.map((b) => (
            <li
              key={b.region}
              className={`flex items-center gap-3 rounded-lg border px-3 py-2 transition ${
                b.dimmed
                  ? "border-wos-border-subtle bg-wos-panel-raised"
                  : "border-wos-accent/60 bg-wos-accent/10"
              }`}
            >
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
                  b.dimmed
                    ? "bg-wos-bg-deep text-wos-text-muted"
                    : "bg-wos-accent/20 text-wos-accent"
                }`}
              >
                {b.index}
              </span>
              <span
                className={`flex-1 truncate text-lg font-medium ${
                  b.dimmed ? "text-wos-text-muted" : "text-wos-text"
                }`}
              >
                {b.text || (b.status === "empty" ? "— no text —" : "reading…")}
              </span>
              {b.confidence != null ? (
                <span className="shrink-0 text-xs tabular-nums text-wos-text-muted">
                  {Math.round(b.confidence * 100)}%
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
