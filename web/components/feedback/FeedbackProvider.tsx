"use client";

import { Icon } from "@/components/ui/Icon";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type FeedbackLevel = "success" | "info";

export type FeedbackToast = {
  id: string;
  message: string;
  level: FeedbackLevel;
  expiresAt: number;
};

const DEFAULT_VISIBLE_MS = 4500;
const TICK_MS = 100;

type FeedbackContextValue = {
  showSuccess: (message: string) => void;
  showInfo: (message: string) => void;
  dismiss: (id: string) => void;
};

const FeedbackContext = createContext<FeedbackContextValue | null>(null);

function pushToast(
  prev: FeedbackToast[],
  message: string,
  level: FeedbackLevel,
): FeedbackToast[] {
  const toast: FeedbackToast = {
    id:
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random()}`,
    message,
    level,
    expiresAt: Date.now() + DEFAULT_VISIBLE_MS,
  };
  return [...prev.slice(-4), toast];
}

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<FeedbackToast[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback((message: string, level: FeedbackLevel) => {
    const trimmed = message.trim();
    if (!trimmed) return;
    setToasts((prev) => pushToast(prev, trimmed, level));
  }, []);

  const showSuccess = useCallback(
    (message: string) => show(message, "success"),
    [show],
  );
  const showInfo = useCallback(
    (message: string) => show(message, "info"),
    [show],
  );

  useEffect(() => {
    if (!toasts.length) return;
    const id = window.setInterval(() => {
      const now = Date.now();
      setToasts((prev) => {
        const next = prev.filter((t) => t.expiresAt > now);
        return next.length === prev.length ? prev : next;
      });
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [toasts.length]);

  const value = useMemo(
    () => ({ showSuccess, showInfo, dismiss }),
    [showSuccess, showInfo, dismiss],
  );

  return (
    <FeedbackContext.Provider value={value}>
      {children}
      <FeedbackToastStack toasts={toasts} onDismiss={dismiss} />
    </FeedbackContext.Provider>
  );
}

function FeedbackToastStack({
  toasts,
  onDismiss,
}: {
  toasts: FeedbackToast[];
  onDismiss: (id: string) => void;
}) {
  if (!toasts.length) return null;

  return (
    <div
      className="feedback-toast-stack"
      role="status"
      aria-live="polite"
      aria-relevant="additions"
    >
      {toasts.map((t) => (
        <FeedbackToastItem key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function FeedbackToastItem({
  toast,
  onDismiss,
}: {
  toast: FeedbackToast;
  onDismiss: (id: string) => void;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, []);

  const remaining = Math.max(0, toast.expiresAt - now);
  const pct = Math.min(100, (remaining / DEFAULT_VISIBLE_MS) * 100);

  return (
    <div className={`feedback-toast feedback-toast--${toast.level}`}>
      <div className="feedback-toast__body">
        <span className="feedback-toast__icon" aria-hidden>
          <Icon name={toast.level === "success" ? "check" : "info"} size="sm" />
        </span>
        <span className="feedback-toast__msg">{toast.message}</span>
        <button
          type="button"
          className="feedback-toast__close"
          aria-label="Dismiss"
          onClick={() => onDismiss(toast.id)}
        >
          <Icon name="close" size="sm" />
        </button>
      </div>
      <div className="feedback-toast__progress-track" aria-hidden>
        <div
          className="feedback-toast__progress-bar"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function useFeedback(): FeedbackContextValue {
  const ctx = useContext(FeedbackContext);
  if (!ctx) {
    throw new Error("useFeedback must be used within FeedbackProvider");
  }
  return ctx;
}
