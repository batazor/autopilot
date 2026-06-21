"use client";

import { useEffect, useState } from "react";
import { OnboardingConfetti } from "@/components/onboarding/OnboardingConfetti";
import {
  checklistCelebrated,
  checklistDismissed,
  fetchOnboardingState,
  markChecklistCelebrated,
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
  { key: "approvals_disabled_at", label: "Disable approvals" },
];

export function OnboardingChecklist() {
  const [state, setState] = useState<OnboardingState | null>(null);
  const [dismissed, setDismissed] = useState(true);
  const [celebrate, setCelebrate] = useState(false);

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

  useEffect(() => {
    if (dismissed || !state) return;
    const complete = ITEMS.every((it) => state[it.key]);
    if (!complete) {
      setCelebrate(false);
      return;
    }
    if (checklistCelebrated()) return;
    markChecklistCelebrated();
    setCelebrate(true);
  }, [dismissed, state]);

  if (dismissed || !state) return null;

  const done = ITEMS.filter((it) => state[it.key]).length;
  const complete = done === ITEMS.length;

  const dismiss = () => {
    markChecklistDismissed();
    setDismissed(true);
  };

  if (complete) {
    return (
      <div className="onboarding-checklist onboarding-checklist--complete">
        <OnboardingConfetti active={celebrate} />
        <div className="onboarding-checklist__header">
          <span className="onboarding-checklist__title">
            First steps complete
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
        <div className="onboarding-checklist__complete">
          <span className="onboarding-checklist__complete-icon" aria-hidden>
            ✓
          </span>
          <span>Ready for regular runs</span>
        </div>
      </div>
    );
  }

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
