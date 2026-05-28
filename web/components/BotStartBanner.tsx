"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
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

export function BotStartBanner() {
  const qc = useQueryClient();
  const [localError, setLocalError] = useState<string | null>(null);

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

  if (botStatus?.running) {
    return (
      <div className="nav-bot-banner" role="region" aria-label="Bot worker">
        <div className="nav-bot-banner__row">
          <button
            type="button"
            className="nav-bot-banner__action"
            disabled={stopMutation.isPending}
            onClick={() => stopMutation.mutate()}
            aria-label={stopMutation.isPending ? "Stopping bot" : "Stop bot"}
            title={stopMutation.isPending ? "Stopping…" : "Stop bot"}
          >
            <Icon name="pause" size="sm" />
          </button>
          <span className="nav-bot-banner__body">
            <span className="nav-bot-banner__title">Bot running</span>
            <span className="nav-bot-banner__desc">
              Mode: {botStatus.mode ?? "unknown"}
              {botStatus.pid ? ` · PID ${botStatus.pid}` : ""}
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
