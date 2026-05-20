"use client";

import { useEffect, useRef } from "react";
import { useDocumentVisible } from "@/lib/hooks";

const DEFAULT_FALLBACK_MS = 15_000;

export type DashboardEventHandler = (
  topic: string,
  data: Record<string, unknown>,
) => void;

type UseDashboardEventStreamOptions = {
  topics: string[];
  instanceId?: string;
  enabled?: boolean;
  onEvent: DashboardEventHandler;
  /** Safety net when SSE is down (tab still visible). */
  fallbackPollMs?: number;
  onFallbackPoll?: () => void | Promise<void>;
};

/**
 * Subscribe to FastAPI SSE (`/api/events/stream`). Refetch handlers run on
 * queue / approval / notification changes instead of 1–2s HTTP polling.
 */
export function useDashboardEventStream({
  topics,
  instanceId,
  enabled = true,
  onEvent,
  fallbackPollMs = DEFAULT_FALLBACK_MS,
  onFallbackPoll,
}: UseDashboardEventStreamOptions): void {
  const visible = useDocumentVisible();
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const onFallbackRef = useRef(onFallbackPoll);
  onFallbackRef.current = onFallbackPoll;

  const topicsKey = [...topics].sort().join(",");

  useEffect(() => {
    if (!enabled || !visible || topics.length === 0) return;

    const params = new URLSearchParams();
    for (const t of topics) params.append("topics", t);
    if (instanceId) params.set("instance_id", instanceId);

    const url = `/api/events/stream?${params.toString()}`;
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let fallbackId: ReturnType<typeof setInterval> | undefined;
    let closed = false;

    const dispatch = (topic: string, raw: string) => {
      try {
        const data = JSON.parse(raw) as Record<string, unknown>;
        onEventRef.current(topic, data);
      } catch {
        onEventRef.current(topic, {});
      }
    };

    const connect = () => {
      if (closed) return;
      es = new EventSource(url);
      for (const topic of topics) {
        es.addEventListener(topic, (ev: MessageEvent) => {
          dispatch(topic, String(ev.data ?? "{}"));
        });
      }
      es.onerror = () => {
        es?.close();
        es = null;
        if (!closed) {
          reconnectTimer = setTimeout(connect, 2000);
        }
      };
    };

    connect();

    if (onFallbackPoll) {
      void onFallbackRef.current?.();
      fallbackId = setInterval(() => {
        void onFallbackRef.current?.();
      }, fallbackPollMs);
    }

    return () => {
      closed = true;
      es?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (fallbackId) clearInterval(fallbackId);
    };
  }, [enabled, visible, topicsKey, instanceId, fallbackPollMs]);
}
