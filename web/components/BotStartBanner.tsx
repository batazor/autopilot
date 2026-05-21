"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { fetchAdbStatus, fetchBotStatus, startLocalBot } from "@/lib/api";
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

  const botStatus = query.data?.bot ?? null;
  const adbStatus = query.data?.adb ?? null;
  const refreshing = query.isFetching;
  const loaded = query.isFetched;

  const adbReadiness: AdbReadiness | null = adbStatus
    ? evaluateAdbReadiness(adbStatus)
    : query.isError
      ? {
          ok: false,
          kind: "scan_error",
          message:
            query.error instanceof Error
              ? query.error.message
              : "Failed to reach API",
        }
      : null;

  const queryError =
    query.isError && query.error instanceof Error ? query.error.message : null;
  const error = localError ?? queryError;

  if (!loaded && !refreshing) {
    return null;
  }

  if (botStatus?.running) {
    return null;
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
        <div className="nav-bot-banner__main">
          <span className="nav-bot-banner__icon" aria-hidden>
            <Icon name="adb" size="sm" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="nav-bot-banner__title">
              {adbReadinessTitle(problem.kind)}
            </span>
            <span className="nav-bot-banner__desc">
              {problem.message}{" "}
              <Link href="/adb" className="nav-bot-banner__link">
                Open ADB
              </Link>{" "}
              to verify emulators and serials in{" "}
              <code>devices.yaml</code> before starting the bot.
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
        <button
          type="button"
          className="nav-bot-banner__btn nav-bot-banner__btn--devices"
          disabled={refreshing}
          onClick={() => void query.refetch()}
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
    );
  }

  return (
    <div className="nav-bot-banner" role="region" aria-label="Bot worker">
      <div className="nav-bot-banner__main">
        <span className="nav-bot-banner__icon" aria-hidden>
          <Icon name="debug-run" size="sm" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="nav-bot-banner__title">Bot not running</span>
          <span className="nav-bot-banner__desc">
            ADB device is online — start workers to drive emulators and run
            scenarios.
          </span>
        </span>
      </div>
      {error ? (
        <p className="nav-bot-banner__error" role="alert">
          {error}
        </p>
      ) : null}
      <button
        type="button"
        className="nav-bot-banner__btn"
        disabled={startMutation.isPending}
        onClick={() => startMutation.mutate()}
      >
        {startMutation.isPending ? "Starting…" : "Start bot"}
      </button>
    </div>
  );
}
