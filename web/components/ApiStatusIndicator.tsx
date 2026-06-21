"use client";

import {
  useApiStatus,
  type ApiConnectivity,
} from "@/components/ApiStatusProvider";

const LABELS: Record<ApiConnectivity, string> = {
  checking: "Checking API…",
  ok: "API connected",
  api_offline: "API offline",
  redis_unreachable: "Redis unreachable",
};

type ApiStatusIndicatorProps = {
  /** Compact single-line label for nav footer. */
  variant?: "footer" | "header";
};

export function ApiStatusIndicator({ variant = "footer" }: ApiStatusIndicatorProps) {
  const { connectivity } = useApiStatus();
  const label = LABELS[connectivity];
  const cls = [
    "api-status",
    `api-status--${connectivity}`,
    variant === "header" ? "api-status--header" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span className={cls} role="status" aria-live="polite" title={label}>
      <span className="api-status__dot" aria-hidden />
      <span className="api-status__label">{label}</span>
    </span>
  );
}
