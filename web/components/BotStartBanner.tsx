"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  fetchAdbStatus,
  fetchBotStatus,
  startLocalBot,
  stopLocalBot,
} from "@/lib/api";
import {
  adbReadinessTitle,
  evaluateAdbReadiness,
  type AdbReadiness,
} from "@/lib/adb-device-ready";
import type { AdbStatus } from "@/lib/config-pages";
import type { BotStatusView } from "@/lib/types";
import { Icon } from "@/components/ui/Icon";

const BOT_POLL_MS = 4000;

type BannerStatus = {
  bot: BotStatusView;
  adb: AdbStatus;
};

async function fetchBannerStatus(): Promise<BannerStatus> {
  const [bot, adb] = await Promise.all([fetchBotStatus(), fetchAdbStatus()]);
  return { bot, adb };
}

function formatProcessAge(startedAt: number | null): string {
  if (!startedAt) return "—";
  const ageSec = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
  if (ageSec < 60) return `${ageSec}s`;
  const m = Math.floor(ageSec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h${m % 60 ? `${m % 60}m` : ""}`;
}

export function BotStartBanner() {
  const qc = useQueryClient();
  const [localError, setLocalError] = useState<string | null>(null);
  // Which supervisor process the operator is currently looking at when
  // more than one is alive (dev rotation, stuck terminate, etc.).
  const [carouselIdx, setCarouselIdx] = useState(0);

  const query = useQuery<BannerStatus>({
    queryKey: ["botStartBanner"],
    queryFn: fetchBannerStatus,
    refetchInterval: BOT_POLL_MS,
  });

  const startMutation = useMutation({
    mutationFn: startLocalBot,
    onSuccess: (view) => {
      qc.setQueryData<BannerStatus>(["botStartBanner"], (prev) =>
        prev ? { ...prev, bot: view } : prev,
      );
      setLocalError(null);
    },
    onError: (e) => {
      setLocalError(e instanceof Error ? e.message : "Failed to start bot");
    },
  });
  const stopMutation = useMutation({
    mutationFn: stopLocalBot,
    onSuccess: (view) => {
      qc.setQueryData<BannerStatus>(["botStartBanner"], (prev) =>
        prev ? { ...prev, bot: view } : prev,
      );
      setLocalError(null);
    },
    onError: (e) => {
      setLocalError(e instanceof Error ? e.message : "Failed to stop bot");
    },
  });

  const botStatus = query.data?.bot ?? null;
  const adbStatus = query.data?.adb ?? null;
  const refreshing = query.isFetching;
  const loaded = query.isFetched;

  const adbReadiness: AdbReadiness | null = adbStatus
    ? evaluateAdbReadiness(adbStatus)
    : null;

  const queryError =
    query.isError && query.error instanceof Error ? query.error.message : null;
  const error = localError ?? queryError;

  if (!loaded && !refreshing) {
    return null;
  }

  if (query.isError && !query.data) {
    return (
      <div
        className="nav-bot-banner nav-bot-banner--offline"
        role="region"
        aria-label="Bot worker"
      >
        <div className="nav-bot-banner__row">
          <span className="nav-bot-banner__icon" aria-hidden>
            <Icon name="warning" size="sm" />
          </span>
          <span className="nav-bot-banner__body">
            <span className="nav-bot-banner__title">API offline</span>
            <span className="nav-bot-banner__desc">
              {queryError ?? "Failed to reach API"}
            </span>
          </span>
        </div>
      </div>
    );
  }

  // Carousel of running supervisor processes (>1 is rare but happens — dev
  // restart left a zombie, accidental double-start, etc.). When only one is
  // running we render exactly the old banner; the extra controls appear from
  // the second process onward.
  const processes = botStatus?.processes ?? [];
  const safeIdx = processes.length > 0
    ? ((carouselIdx % processes.length) + processes.length) % processes.length
    : 0;
  // Clamp the index back into range whenever a process disappears (Stop was
  // pressed, dev tool killed it, etc.) — otherwise we'd index past the array
  // and show empty PID / mode.
  useEffect(() => {
    if (processes.length > 0 && carouselIdx >= processes.length) {
      setCarouselIdx(0);
    }
  }, [processes.length, carouselIdx]);
  const currentProc = processes[safeIdx] ?? null;
  const currentPid = currentProc?.pid ?? botStatus?.pid ?? null;

  if (botStatus?.running) {
    const multi = processes.length > 1;
    return (
      <div className="nav-bot-banner" role="region" aria-label="Bot worker">
        <div className="nav-bot-banner__row">
          <button
            type="button"
            className="nav-bot-banner__action"
            disabled={stopMutation.isPending}
            onClick={() => stopMutation.mutate()}
            aria-label={stopMutation.isPending ? "Stopping bot" : "Stop bot"}
            title={
              stopMutation.isPending
                ? "Stopping…"
                : multi
                  ? `Stop bot (terminates all ${processes.length} supervisors)`
                  : "Stop bot"
            }
          >
            <Icon name="pause" size="sm" />
          </button>
          <span className="nav-bot-banner__body">
            <span className="nav-bot-banner__title">
              Bot running
              {multi ? (
                <span className="nav-bot-banner__badge" aria-label={`${safeIdx + 1} of ${processes.length} supervisors`}>
                  {safeIdx + 1}/{processes.length}
                </span>
              ) : null}
            </span>
            <span className="nav-bot-banner__desc">
              Mode: {botStatus.mode ?? "unknown"}
              {currentPid ? ` · PID ${currentPid}` : ""}
              {currentProc?.started_at
                ? ` · up ${formatProcessAge(currentProc.started_at)}`
                : ""}
            </span>
          </span>
          {multi ? (
            <button
              type="button"
              className="nav-bot-banner__action"
              onClick={() => setCarouselIdx((i) => (i + 1) % processes.length)}
              aria-label="Show next supervisor"
              title={`Next supervisor (${safeIdx + 1}/${processes.length})`}
            >
              <Icon name="chevron-right" size="sm" />
            </button>
          ) : null}
        </div>
        {error ? (
          <p className="nav-bot-banner__error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    );
  }

  if (!adbReadiness?.ok) {
    const problem = adbReadiness ?? {
      ok: false as const,
      kind: "no_devices" as const,
      message: "Checking ADB…",
    };
    return (
      <div
        className="nav-bot-banner nav-bot-banner--devices"
        role="region"
        aria-label="ADB devices"
      >
        <div className="nav-bot-banner__row">
          <span className="nav-bot-banner__icon" aria-hidden>
            <Icon name="adb" size="sm" />
          </span>
          <span className="nav-bot-banner__body">
            <span className="nav-bot-banner__title">
              {adbReadinessTitle(problem.kind)}
            </span>
            <span className="nav-bot-banner__desc">
              {problem.message}{" "}
              <Link href="/adb" className="nav-bot-banner__link">
                Open ADB
              </Link>
            </span>
          </span>
        </div>
        {adbStatus?.configured.length ? (
          <p className="nav-bot-banner__meta">
            Configured: {adbStatus.configured.length} · Live:{" "}
            {adbStatus.live_devices.length}
          </p>
        ) : null}
        {error ? (
          <p className="nav-bot-banner__error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="nav-bot-banner" role="region" aria-label="Bot worker">
      <div className="nav-bot-banner__row">
        <button
          type="button"
          className="nav-bot-banner__action"
          disabled={startMutation.isPending}
          onClick={() => startMutation.mutate()}
          aria-label={startMutation.isPending ? "Starting bot" : "Start bot"}
          title={startMutation.isPending ? "Starting…" : "Start bot"}
        >
          <Icon name="play" size="sm" />
        </button>
        <span className="nav-bot-banner__body">
          <span className="nav-bot-banner__title">Bot not running</span>
          <span className="nav-bot-banner__desc">
            ADB online — start workers to run scenarios.
          </span>
        </span>
      </div>
      {error ? (
        <p className="nav-bot-banner__error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
