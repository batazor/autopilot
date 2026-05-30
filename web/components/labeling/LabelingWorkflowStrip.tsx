"use client";

import { Fragment, type ReactNode } from "react";

import type { LabelingWorkflowStep } from "@/lib/labeling-utils";

type Props = {
  steps: LabelingWorkflowStep[];
};

/** Render minimal `**bold**` markdown inline (the only markup used in step details). */
function renderDetail(text: string): ReactNode {
  return text.split(/(\*\*[^*]+\*\*)/).map((part, i) => {
    const bold = part.match(/^\*\*([^*]+)\*\*$/);
    return bold ? (
      <strong key={i}>{bold[1]}</strong>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    );
  });
}

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
            <span className="labeling-step__detail">{renderDetail(step.detail)}</span>
          ) : null}
        </div>
      ))}
    </div>
  );
}
