"use client";

import { useEffect, useRef } from "react";
import { useDocumentVisible } from "@/lib/hooks";

const DEFAULT_FALLBACK_MS = 15_000;
const DEFAULT_DEBOUNCE_MS = 100;

export type DashboardEventHandler = (
  topic: string,
  data: Record<string, unknown>,
) => void;

type UseDashboardEventStreamOptions = {
  topics: string[];
  instanceId?: string;
  playerId?: string;
  enabled?: boolean;
  onEvent: DashboardEventHandler;
  /** Coalesce rapid SSE events (0 = disabled). */
  debounceMs?: number;
  /** Safety net when SSE is down (tab still visible). */
  fallbackPollMs?: number;
  onFallbackPoll?: () => void | Promise<void>;
};

/**
 * Subscribe to FastAPI SSE (`/api/events/stream`). Refetch handlers run on
 * fleet / instance / player / queue / approval / notification changes instead of
 * fixed-interval HTTP polling.
 */
export function useDashboardEventStream({
  topics,
  instanceId,
  playerId,
  enabled = true,
  onEvent,
  debounceMs = DEFAULT_DEBOUNCE_MS,
  fallbackPollMs = DEFAULT_FALLBACK_MS,
  onFallbackPoll,
}: UseDashboardEventStreamOptions): void {
  const visible = useDocumentVisible();
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const onFallbackRef = useRef(onFallbackPoll);
  onFallbackRef.current = onFallbackPoll;
  const wasVisibleRef = useRef(visible);

  const topicsKey = [...topics].sort().join(",");

  useEffect(() => {
    if (!enabled || !onFallbackPoll) return;
    if (visible && !wasVisibleRef.current) {
      void onFallbackRef.current?.();
    }
    wasVisibleRef.current = visible;
  }, [enabled, visible, onFallbackPoll]);

  useEffect(() => {
    if (!enabled || !visible || topics.length === 0) return;

    const params = new URLSearchParams();
    for (const t of topics) params.append("topics", t);
    if (instanceId) params.set("instance_id", instanceId);
    if (playerId) params.set("player_id", playerId);

    const url = `/api/events/stream?${params.toString()}`;
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let fallbackId: ReturnType<typeof setInterval> | undefined;
    let debounceTimer: ReturnType<typeof setTimeout> | undefined;
    const pendingEvents = new Map<string, Record<string, unknown>>();
    let closed = false;

    const flushPendingEvents = () => {
      debounceTimer = undefined;
      if (pendingEvents.size === 0) return;
      const batch = [...pendingEvents.entries()];
      pendingEvents.clear();
      for (const [topic, data] of batch) {
        onEventRef.current(topic, data);
      }
    };

    const dispatch = (topic: string, raw: string) => {
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        data = {};
      }
      if (debounceMs <= 0) {
        onEventRef.current(topic, data);
        return;
      }
      pendingEvents.set(topic, data);
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(flushPendingEvents, debounceMs);
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
      if (debounceTimer) clearTimeout(debounceTimer);
      pendingEvents.clear();
    };
  }, [
    enabled,
    visible,
    topicsKey,
    instanceId,
    playerId,
    fallbackPollMs,
    debounceMs,
    onFallbackPoll,
  ]);
}
