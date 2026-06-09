"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchAttention } from "@/lib/api";

export const ATTENTION_KEY = ["attention"] as const;
const POLL_MS = 30_000;

/**
 * Shared attention feed. The global banner and the overview panel both mount
 * this; the common query key means one request serves every consumer.
 *
 * Stale data on API hiccups is intentional (placeholderData keeps the last
 * frame): a problem list flashing away because one poll failed reads as
 * "everything fixed itself", which is worse than a 30s-old list.
 */
export function useAttention() {
  return useQuery({
    queryKey: ATTENTION_KEY,
    queryFn: fetchAttention,
    refetchInterval: POLL_MS,
    placeholderData: (prev) => prev,
    retry: false,
  });
}
