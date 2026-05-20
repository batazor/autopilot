"use client";

import { AppListbox } from "@/components/headless";
import { SWIPE_DIRECTIONS, detectStepType, type ScenarioStep } from "@/lib/edit-dsl/dsl";
import { SelectWithFreetext } from "./SelectWithFreetext";
import { StepsList } from "./StepsList";

export type EditorMeta = {
  regions: string[];
  fsm_nodes: string[];
  exec_names: string[];
  scenario_keys: string[];
};

type Props = {
  step: ScenarioStep;
  path: number[];
  depth: number;
  meta: EditorMeta;
  onChange: (step: ScenarioStep) => void;
};

function setCond(step: ScenarioStep, cond: string, stype: string) {
  const trimmed = cond.trim();
  if (trimmed) step.cond = trimmed;
  else if ("cond" in step && stype !== "cond") delete step.cond;
}

export function StepCard({ step, path, depth, meta, onChange }: Props) {
  const stype = detectStepType(step);
  const pk = path.join("/");

  const update = (patch: ScenarioStep) => {
    onChange({ ...step, ...patch });
  };

  const patchStep = (mutate: (s: ScenarioStep) => void) => {
    const next = { ...step };
    mutate(next);
    onChange(next);
  };

  return (
    <div className="edit-scenario-step-fields">
      <label className="field-row">
        <span>cond (guard, optional)</span>
        <input
          value={String(step.cond ?? "")}
          placeholder='e.g. currentNode == main_city'
          onChange={(e) =>
            patchStep((s) => {
              setCond(s, e.target.value, stype);
            })
          }
        />
      </label>

      {stype === "click" && (
        <SelectWithFreetext
          label="region (click)"
          value={String(step.click ?? "")}
          options={meta.regions}
          onChange={(v) => update({ click: v })}
        />
      )}

      {stype === "long_click" && (
        <div className="form-grid-2">
          <SelectWithFreetext
            label="region (long_click)"
            value={String(step.long_click ?? "")}
            options={meta.regions}
            onChange={(v) => update({ long_click: v })}
          />
          <label className="field-row">
            <span>duration (e.g. 800ms)</span>
            <input
              value={String(step.wait ?? "800ms")}
              onChange={(e) => update({ wait: e.target.value })}
            />
          </label>
        </div>
      )}

      {stype === "match" && (
        <>
          <SelectWithFreetext
            label="region (match)"
            value={String(step.match ?? "")}
            options={meta.regions}
            onChange={(v) => update({ match: v })}
          />
          <MatchParams step={step} onChange={onChange} pk={pk} />
        </>
      )}

      {stype === "while_match" && (
        <WhileMatchBlock step={step} path={path} depth={depth} meta={meta} onChange={onChange} pk={pk} />
      )}

      {stype === "ocr" && (
        <SelectWithFreetext
          label="region (ocr)"
          value={String(step.ocr ?? "")}
          options={meta.regions}
          onChange={(v) => update({ ocr: v })}
        />
      )}

      {stype === "exec" && (
        <SelectWithFreetext
          label="function"
          value={String(step.exec ?? "")}
          options={meta.exec_names}
          onChange={(v) => update({ exec: v })}
        />
      )}

      {stype === "push_scenario" && (
        <PushScenarioFields step={step} meta={meta} onChange={onChange} pk={pk} />
      )}

      {stype === "wait" && (
        <label className="field-row">
          <span>duration (e.g. 500ms, 2s)</span>
          <input
            value={String(step.wait ?? "1s")}
            onChange={(e) => update({ wait: e.target.value })}
          />
        </label>
      )}

      {stype === "swipe_direction" && (
        <SwipeDirectionFields step={step} onChange={onChange} pk={pk} />
      )}

      {(stype === "loop" || stype === "repeat") && (
        <LoopBlock step={step} stype={stype} path={path} depth={depth} meta={meta} onChange={onChange} pk={pk} />
      )}

      {stype === "cond" && (
        <div>
          <p className="muted">Composite cond block — inner steps run only if guard above is true.</p>
          <StepsList
            steps={Array.isArray(step.steps) ? (step.steps as ScenarioStep[]) : []}
            parentPath={path}
            depth={depth + 1}
            parentKind="cond"
            meta={meta}
            onStepsChange={(steps) => update({ steps })}
          />
        </div>
      )}

      {stype === "break" && (
        <label className="field-row">
          <span>label</span>
          <input
            value={String(step.break ?? "loop")}
            onChange={(e) => update({ break: e.target.value })}
          />
        </label>
      )}

      {stype === "?" && (
        <p className="error-banner">Unknown step type — keys: {Object.keys(step).join(", ")}</p>
      )}
    </div>
  );
}

function MatchParams({
  step,
  onChange,
  pk,
}: {
  step: ScenarioStep;
  onChange: (s: ScenarioStep) => void;
  pk: string;
}) {
  const thr = Number(step.threshold ?? 0);
  const sat = Number(step.min_match_saturation ?? 0);
  return (
    <div className="form-grid-2" key={pk}>
      <label className="field-row">
        <span>threshold (0 = default)</span>
        <input
          type="number"
          min={0}
          max={1}
          step={0.05}
          value={thr || ""}
          placeholder="0"
          onChange={(e) => {
            const next = { ...step };
            const v = parseFloat(e.target.value);
            if (v > 0) next.threshold = v;
            else delete next.threshold;
            onChange(next);
          }}
        />
      </label>
      <label className="field-row">
        <span>min_match_saturation (0 = off)</span>
        <input
          type="number"
          min={0}
          max={100}
          value={sat || ""}
          placeholder="0"
          onChange={(e) => {
            const next = { ...step };
            const v = parseInt(e.target.value, 10);
            if (v > 0) next.min_match_saturation = v;
            else delete next.min_match_saturation;
            onChange(next);
          }}
        />
      </label>
    </div>
  );
}

function WhileMatchBlock({
  step,
  path,
  depth,
  meta,
  onChange,
  pk,
}: {
  step: ScenarioStep;
  path: number[];
  depth: number;
  meta: EditorMeta;
  onChange: (s: ScenarioStep) => void;
  pk: string;
}) {
  const inner = Array.isArray(step.steps) ? (step.steps as ScenarioStep[]) : [];
  const sat = Number(step.min_match_saturation ?? 0);
  return (
    <>
      <SelectWithFreetext
        label="region (while_match)"
        value={String(step.while_match ?? "")}
        options={meta.regions}
        onChange={(v) => onChange({ ...step, while_match: v })}
      />
      <div className="form-grid-2">
        <label className="field-row">
          <span>max iterations</span>
          <input
            type="number"
            min={0}
            max={999}
            value={Number(step.max ?? 5)}
            onChange={(e) => onChange({ ...step, max: parseInt(e.target.value, 10) || 0 })}
          />
        </label>
        <label className="field-row">
          <span>min_match_saturation</span>
          <input
            type="number"
            min={0}
            max={100}
            value={sat || ""}
            placeholder="0"
            onChange={(e) => {
              const next = { ...step };
              const v = parseInt(e.target.value, 10);
              if (v > 0) next.min_match_saturation = v;
              else delete next.min_match_saturation;
              onChange(next);
            }}
          />
        </label>
      </div>
      <p className="muted">Inner steps (run on each iteration):</p>
      <StepsList
        steps={inner}
        parentPath={path}
        depth={depth + 1}
        parentKind="while_match"
        meta={meta}
        onStepsChange={(steps) => onChange({ ...step, steps })}
      />
    </>
  );
}

function LoopBlock({
  step,
  stype,
  path,
  depth,
  meta,
  onChange,
  pk,
}: {
  step: ScenarioStep;
  stype: "loop" | "repeat";
  path: number[];
  depth: number;
  meta: EditorMeta;
  onChange: (s: ScenarioStep) => void;
  pk: string;
}) {
  let spec = step[stype];
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) {
    spec = { max: typeof spec === "number" ? spec : 1, steps: [] };
  }
  const s = spec as Record<string, unknown>;
  const inner = Array.isArray(s.steps) ? (s.steps as ScenarioStep[]) : [];
  const max = Number(s.max ?? 1);

  const setSpec = (patch: Record<string, unknown>) => {
    onChange({ ...step, [stype]: { ...s, ...patch } });
  };

  return (
    <div key={pk}>
      <label className="field-row">
        <span>max iterations</span>
        <input
          type="number"
          min={0}
          max={999}
          value={max}
          onChange={(e) => setSpec({ max: parseInt(e.target.value, 10) || 0 })}
        />
      </label>
      <p className="muted">Inner steps (use `break: loop` to exit early):</p>
      <StepsList
        steps={inner}
        parentPath={path}
        depth={depth + 1}
        parentKind={stype}
        meta={meta}
        onStepsChange={(steps) => setSpec({ steps })}
      />
    </div>
  );
}

function PushScenarioFields({
  step,
  meta,
  onChange,
  pk,
}: {
  step: ScenarioStep;
  meta: EditorMeta;
  onChange: (s: ScenarioStep) => void;
  pk: string;
}) {
  const cur = step.push_scenario;
  let nameV = "";
  let prioV = 0;
  if (cur && typeof cur === "object" && !Array.isArray(cur)) {
    nameV = String((cur as Record<string, unknown>).name ?? "");
    prioV = Number((cur as Record<string, unknown>).priority ?? 0);
  } else {
    nameV = String(cur ?? "");
  }

  return (
    <div className="form-grid-2" key={pk}>
      <SelectWithFreetext
        label="scenario key"
        value={nameV}
        options={meta.scenario_keys}
        onChange={(name) => {
          const next = { ...step };
          if (prioV) next.push_scenario = { name, priority: prioV };
          else next.push_scenario = name;
          onChange(next);
        }}
      />
      <label className="field-row">
        <span>priority (0 = inherit)</span>
        <input
          type="number"
          min={0}
          step={1000}
          value={prioV || ""}
          placeholder="0"
          onChange={(e) => {
            const next = { ...step };
            const p = parseInt(e.target.value, 10) || 0;
            if (p) next.push_scenario = { name: nameV, priority: p };
            else next.push_scenario = nameV;
            onChange(next);
          }}
        />
      </label>
    </div>
  );
}

function SwipeDirectionFields({
  step,
  onChange,
  pk,
}: {
  step: ScenarioStep;
  onChange: (s: ScenarioStep) => void;
  pk: string;
}) {
  let spec = step.swipe_direction;
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) {
    spec = { direction: "up", delta: 400, duration_ms: 600 };
  }
  const s = spec as Record<string, unknown>;
  const dir = String(s.direction ?? "up");
  const dirIdx = SWIPE_DIRECTIONS.includes(dir as (typeof SWIPE_DIRECTIONS)[number]) ? dir : "up";

  return (
    <div className="form-grid-3" key={pk}>
      <label className="field-row">
        <span>direction</span>
        <AppListbox
          value={dirIdx}
          onChange={(v) =>
            onChange({
              ...step,
              swipe_direction: { ...s, direction: v },
            })
          }
          options={SWIPE_DIRECTIONS.map((d) => ({ value: d, label: d }))}
          minWidth={120}
        />
      </label>
      <label className="field-row">
        <span>delta (px)</span>
        <input
          type="number"
          min={10}
          max={2000}
          value={Number(s.delta ?? 400)}
          onChange={(e) =>
            onChange({
              ...step,
              swipe_direction: { ...s, delta: parseInt(e.target.value, 10) || 400 },
            })
          }
        />
      </label>
      <label className="field-row">
        <span>duration_ms</span>
        <input
          type="number"
          min={50}
          max={5000}
          value={Number(s.duration_ms ?? 600)}
          onChange={(e) =>
            onChange({
              ...step,
              swipe_direction: { ...s, duration_ms: parseInt(e.target.value, 10) || 600 },
            })
          }
        />
      </label>
    </div>
  );
}
