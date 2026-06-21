"use client";

import { useRef } from "react";
import { Icon } from "@/components/ui";
import { toastLevelIcon } from "@/lib/approvals/format";
import { type Toast, TOAST_VISIBLE_MS } from "@/lib/approvals/types";

export function ApprovalToast({
  toast,
  now,
  onDismiss,
  onExtend,
}: {
  toast: Toast;
  now: number;
  onDismiss: () => void;
  onExtend: (extraMs: number) => void;
}) {
  const pauseStartRef = useRef<number | null>(null);
  const remaining = Math.max(0, toast.expiresAt - now);
  const progress = Math.min(100, (remaining / TOAST_VISIBLE_MS) * 100);

  const handlePointerEnter = () => {
    pauseStartRef.current = Date.now();
  };
  const handlePointerLeave = () => {
    if (pauseStartRef.current == null) return;
    onExtend(Date.now() - pauseStartRef.current);
    pauseStartRef.current = null;
  };

  return (
    <div
      className={`approvals-toast approvals-toast--${toast.level}`}
      onPointerEnter={handlePointerEnter}
      onPointerLeave={handlePointerLeave}
    >
      <div className="approvals-toast__body">
        <span className="approvals-toast__icon" aria-hidden>
          <Icon name={toastLevelIcon(toast.level)} size="sm" />
        </span>
        <span className="approvals-toast__msg">{toast.message}</span>
        <button
          type="button"
          className="approvals-toast__close"
          onClick={onDismiss}
          aria-label="Dismiss notification"
        >
          <Icon name="close" size="sm" />
        </button>
      </div>
      <div
        className="approvals-toast__progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress)}
        aria-label="Notification auto-dismiss"
      >
        <div
          className="approvals-toast__progress-bar"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}
