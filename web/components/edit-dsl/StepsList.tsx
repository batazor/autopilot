"use client";

import { useState } from "react";
import { AppListbox } from "@/components/headless";
import {
  LOOP_PARENT_KINDS,
  STEP_TYPES_FOR_NEW,
  detectStepType,
  newStep,
  stepSummary,
  type ScenarioStep,
} from "@/lib/edit-dsl/dsl";
import type { EditorMeta } from "./StepCard";
import { StepCard } from "./StepCard";

type Props = {
  steps: ScenarioStep[];
  parentPath: number[];
  depth: number;
  parentKind?: string;
  meta: EditorMeta;
  onStepsChange: (steps: ScenarioStep[]) => void;
};

function moveStep(steps: ScenarioStep[], idx: number, delta: number): ScenarioStep[] {
  const j = idx + delta;
  if (j < 0 || j >= steps.length) return steps;
  const next = [...steps];
  [next[idx], next[j]] = [next[j], next[idx]];
  return next;
}

export function StepsList({
  steps,
  parentPath,
  depth,
  parentKind = "",
  meta,
  onStepsChange,
}: Props) {
  const [addType, setAddType] = useState<string>(STEP_TYPES_FOR_NEW[0]);

  const availableTypes = [
    ...STEP_TYPES_FOR_NEW,
    ...(LOOP_PARENT_KINDS.has(parentKind) ? (["break"] as const) : []),
  ];

  const updateStep = (idx: number, step: ScenarioStep) => {
    const next = [...steps];
    next[idx] = step;
    onStepsChange(next);
  };

  const removeStep = (idx: number) => {
    onStepsChange(steps.filter((_, i) => i !== idx));
  };

  return (
    <div className="edit-scenario-steps-list">
      {steps.map((step, i) => {
        const path = [...parentPath, i];
        const stype = detectStepType(step);
        const subt = stepSummary(step).trim();
        return (
          <div key={path.join("/")} className="edit-scenario-step-card panel">
            <div className="edit-scenario-step-head">
              <strong>{i + 1}.</strong>
              <code>{stype}</code>
              {subt && <span className="muted">{subt.slice(0, 120)}</span>}
              <span className="edit-scenario-step-actions">
                <button
                  type="button"
                  className="btn-icon"
                  disabled={i === 0}
                  onClick={() => onStepsChange(moveStep(steps, i, -1))}
                  title="Move up"
                >
                  ↑
                </button>
                <button
                  type="button"
                  className="btn-icon"
                  disabled={i === steps.length - 1}
                  onClick={() => onStepsChange(moveStep(steps, i, 1))}
                  title="Move down"
                >
                  ↓
                </button>
                <button
                  type="button"
                  className="btn-icon"
                  onClick={() => removeStep(i)}
                  title="Remove"
                >
                  ✕
                </button>
              </span>
            </div>
            <StepCard
              step={step}
              path={path}
              depth={depth}
              meta={meta}
              onChange={(s) => updateStep(i, s)}
            />
          </div>
        );
      })}

      <div className="toolbar edit-scenario-add-row">
        <AppListbox
          value={addType}
          onChange={setAddType}
          options={availableTypes.map((t) => ({ value: t, label: t }))}
          minWidth={140}
        />
        <button
          type="button"
          className="btn-secondary"
          onClick={() => onStepsChange([...steps, newStep(addType)])}
        >
          Add step
        </button>
      </div>
    </div>
  );
}
