"use client";

import { useCallback, useState } from "react";
import { fetchBotStatus, startLocalBot } from "@/lib/api";
import { usePollWhenVisible } from "@/lib/hooks";
import type { BotStatusView } from "@/lib/types";
import { Icon } from "@/components/ui/Icon";

const BOT_POLL_MS = 4000;

export function BotStartBanner() {
  const [status, setStatus] = useState<BotStatusView | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const view = await fetchBotStatus();
      setStatus(view);
      setError(null);
    } catch {
      setStatus(null);
    }
  }, []);

  usePollWhenVisible(refresh, BOT_POLL_MS);

  if (status?.running) {
    return null;
  }

  const onStart = async () => {
    setStarting(true);
    setError(null);
    try {
      const view = await startLocalBot();
      setStatus(view);
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
            Start workers to drive emulators and run scenarios.
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
