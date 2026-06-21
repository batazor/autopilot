import type { ScenarioProgress } from "@/lib/types";
import { scenarioProgressLabel } from "@/lib/approvals/format";

export function ScenarioProgressBar({ progress }: { progress: ScenarioProgress }) {
  const completedSteps =
    progress.completed_steps ??
    (progress.step_total > 0
      ? progress.is_navigating
        ? progress.step_current
        : progress.is_running
          ? progress.step_current + 1
          : progress.step_current
      : 0);
  const ratio =
    progress.progress_ratio != null
      ? Math.min(100, progress.progress_ratio * 100)
      : progress.step_total > 0
        ? Math.min(100, (completedSteps / progress.step_total) * 100)
        : 0;
  const label = scenarioProgressLabel(progress);
  const currentIdx =
    progress.highlight_step_index ??
    (progress.is_running && !progress.is_navigating ? progress.step_current : -1);

  return (
    <div className="approvals-scenario-progress">
      <div
        className="approvals-scenario-progress__track"
        role="progressbar"
        aria-label="Scenario step progress"
        aria-valuemin={0}
        aria-valuemax={Math.max(progress.step_total, 1)}
        aria-valuenow={completedSteps}
      >
        <div
          className="approvals-scenario-progress__bar"
          style={{ width: `${ratio}%` }}
        />
      </div>
      <span className="approvals-scenario-progress__label meta">{label}</span>
      {progress.step_summaries.length > 0 ? (
        <p className="approvals-scenario-progress__steps meta">
          {progress.step_summaries.map((summary, i) => (
            <span key={`${summary}-${i}`}>
              {i > 0 ? " · " : null}
              {i === currentIdx ? <strong>{summary}</strong> : summary}
            </span>
          ))}
        </p>
      ) : null}
    </div>
  );
}
