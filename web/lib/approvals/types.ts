import type { NotificationEvent } from "@/lib/types";

export type Toast = NotificationEvent & { createdAt: number; expiresAt: number };

export type Decision = "approve" | "reject" | "skip";

// Track which control is currently in flight so we can disable only it
// (operators routinely change their mind between approve/reject before the
// previous request returns, and the old "global busy flag" pattern made
// that impossible).
export type BusyAction =
  | Decision
  | "toggle"
  | "clear-pending"
  | "clear-queue"
  | "reset-screen"
  | "reset-player"
  | "test-module"
  | null;

export type ImageSource = "capture" | "live";

export const NOTIFICATIONS_MAX_AGE_S = 30;
export const TOAST_VISIBLE_MS = 6000;
export const TICK_MS = 100;
export const DOCUMENT_TITLE_BASE = "Click approvals · Autopilot";

export const SCREENSHOT_SOURCE_OPTIONS = [
  { value: "capture", label: "Captured (request)" },
  { value: "live", label: "Live rolling" },
];

export const TEST_MODULE_ALL = "";
