"use client";

import type { LabelingWorkflowStep } from "@/lib/labeling-utils";

type Props = {
  steps: LabelingWorkflowStep[];
};

export function LabelingWorkflowStrip({ steps }: Props) {
  if (!steps.length) return null;
  return (
    <div className="labeling-workflow" role="list">
      {steps.map((step) => (
        <div
          key={step.key}
          role="listitem"
          className={`labeling-step ${step.done ? "labeling-step--done" : "labeling-step--pending"}`}
        >
          <span className="labeling-step__icon">{step.done ? "✓" : "○"}</span>
          <span className="labeling-step__label">{step.label}</span>
          {step.detail ? (
            <span className="labeling-step__detail">{step.detail}</span>
          ) : null}
        </div>
      ))}
    </div>
  );
}
