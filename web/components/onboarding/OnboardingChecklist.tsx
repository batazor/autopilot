"use client";

import { useEffect, useState } from "react";
import {
  checklistDismissed,
  fetchOnboardingState,
  markChecklistDismissed,
  type OnboardingState,
} from "@/lib/onboarding";

type Item = {
  key: keyof OnboardingState;
  label: string;
};

const ITEMS: readonly Item[] = [
  { key: "device_added_at", label: "Add device" },
  { key: "bot_started_at", label: "Start bot" },
  { key: "first_scenario_at", label: "Wait for first scenario" },
  { key: "first_approval_at", label: "Approve first click" },
  { key: "first_ocr_at", label: "View first OCR result" },
];

export function OnboardingChecklist() {
  const [state, setState] = useState<OnboardingState | null>(null);
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    setDismissed(checklistDismissed());
  }, []);

  useEffect(() => {
    if (dismissed) return;
    let cancelled = false;
    const pull = () => {
      fetchOnboardingState()
        .then((s) => {
          if (!cancelled) setState(s);
        })
        .catch(() => {});
    };
    pull();
    const id = window.setInterval(pull, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [dismissed]);

  if (dismissed || !state) return null;

  const done = ITEMS.filter((it) => state[it.key]).length;
  if (done === ITEMS.length) return null;

  const dismiss = () => {
    markChecklistDismissed();
    setDismissed(true);
  };

  return (
    <div className="onboarding-checklist">
      <div className="onboarding-checklist__header">
        <span className="onboarding-checklist__title">
          First steps ({done}/{ITEMS.length})
        </span>
        <button
          type="button"
          className="onboarding-checklist__dismiss"
          onClick={dismiss}
          aria-label="Dismiss checklist"
          title="Dismiss"
        >
          ×
        </button>
      </div>
      <ul className="onboarding-checklist__list">
        {ITEMS.map((it) => {
          const ok = Boolean(state[it.key]);
          return (
            <li
              key={it.key}
              className={[
                "onboarding-checklist__item",
                ok ? "is-done" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <span
                className="onboarding-checklist__bullet"
                aria-hidden
              >
                {ok ? "✓" : "○"}
              </span>
              <span>{it.label}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
