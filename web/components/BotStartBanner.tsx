"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { fetchAdbStatus, fetchBotStatus, startLocalBot } from "@/lib/api";
import {
  adbReadinessTitle,
  evaluateAdbReadiness,
  type AdbReadiness,
} from "@/lib/adb-device-ready";
import type { AdbStatus } from "@/lib/config-pages";
import { usePollWhenVisible } from "@/lib/hooks";
import type { BotStatusView } from "@/lib/types";
import { Icon } from "@/components/ui/Icon";

const BOT_POLL_MS = 4000;

export function BotStartBanner() {
  const [botStatus, setBotStatus] = useState<BotStatusView | null>(null);
  const [adbStatus, setAdbStatus] = useState<AdbStatus | null>(null);
  const [adbReadiness, setAdbReadiness] = useState<AdbReadiness | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [bot, adb] = await Promise.all([fetchBotStatus(), fetchAdbStatus()]);
      setBotStatus(bot);
      setAdbStatus(adb);
      setAdbReadiness(evaluateAdbReadiness(adb));
      setError(null);
    } catch (e) {
      setBotStatus(null);
      setAdbStatus(null);
      setAdbReadiness({
        ok: false,
        kind: "scan_error",
        message: e instanceof Error ? e.message : "Failed to reach API",
      });
    } finally {
      setLoaded(true);
      setRefreshing(false);
    }
  }, []);

  usePollWhenVisible(refresh, BOT_POLL_MS);

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
          onClick={() => void refresh()}
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
    );
  }

  const onStart = async () => {
    setStarting(true);
    setError(null);
    try {
      const view = await startLocalBot();
      setBotStatus(view);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start bot");
    } finally {
      setStarting(false);
    }
  };

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
        disabled={starting}
        onClick={() => void onStart()}
      >
        {starting ? "Starting…" : "Start bot"}
      </button>
    </div>
  );
}
