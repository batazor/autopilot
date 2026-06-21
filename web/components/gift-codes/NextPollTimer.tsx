"use client";

import { useEffect, useState } from "react";
import { fetchBotStatus, fetchGiftCodePollStatus } from "@/lib/api";
import { formatDuration } from "@/lib/gift-codes/types";

// Live countdown to the next scheduler-driven scrape for `game`. The backend
// returns the remaining TTL of the cadence key; we anchor it to an absolute
// target so a 1s tick stays accurate, and re-fetch every 30s (the scheduler
// tick cadence) to resync and pick up a reset after a cycle fires.
export function NextPollTimer({ game }: { game: string }) {
  const [intervalSeconds, setIntervalSeconds] = useState<number | null>(null);
  const [targetMs, setTargetMs] = useState<number | null>(null);
  const [unknown, setUnknown] = useState(false);
  // The scheduler that drives auto-scrape runs inside the bot worker, so its
  // running state is what makes the countdown meaningful. null = unknown yet.
  const [schedulerRunning, setSchedulerRunning] = useState<boolean | null>(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const s = await fetchGiftCodePollStatus(game);
        if (!alive) return;
        setIntervalSeconds(s.interval_seconds);
        if (s.next_poll_seconds === null) {
          setUnknown(true);
          setTargetMs(null);
        } else {
          setUnknown(false);
          setTargetMs(Date.now() + s.next_poll_seconds * 1000);
        }
      } catch {
        if (!alive) return;
        setUnknown(true);
        setTargetMs(null);
      }
      try {
        const bot = await fetchBotStatus();
        if (alive) setSchedulerRunning(bot.running);
      } catch {
        if (alive) setSchedulerRunning(null);
      }
    };
    load();
    const refetch = setInterval(load, 30_000);
    const tick = setInterval(() => setTick((n) => n + 1), 1000);
    return () => {
      alive = false;
      clearInterval(refetch);
      clearInterval(tick);
    };
  }, [game]);

  const everyLabel =
    intervalSeconds !== null
      ? `Auto-scrape every ${Math.round(intervalSeconds / 3600)}h · `
      : "";

  let countdown: string;
  if (unknown) {
    countdown = "next run: unknown";
  } else if (targetMs === null) {
    countdown = "next run: due now";
  } else {
    const remaining = Math.max(0, (targetMs - Date.now()) / 1000);
    countdown =
      remaining < 1 ? "next run: due now" : `next run in ${formatDuration(remaining)}`;
  }

  return (
    <p className="mb-3 flex flex-wrap items-center gap-2 text-sm text-wos-text-muted">
      <span>
        {everyLabel}
        {countdown}
      </span>
      {schedulerRunning === false ? (
        <span
          className="status-pill pill-paused"
          title="The bot worker (which runs the scheduler) is stopped, so auto-scrape won't fire until it's started."
        >
          scheduler stopped
        </span>
      ) : schedulerRunning === true ? (
        <span className="status-pill pill-live" title="Scheduler is running.">
          scheduler active
        </span>
      ) : null}
    </p>
  );
}
