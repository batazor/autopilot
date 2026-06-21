"use client";

import { AppListbox } from "@/components/headless";
import { editDslRegionPreviewUrl } from "@/lib/api";
import { SWIPE_DIRECTIONS, detectStepType, type ScenarioStep } from "@/lib/edit-dsl/dsl";
import { SelectWithFreetext } from "./SelectWithFreetext";

const regionPreview = (value: string) =>
  value.trim() ? editDslRegionPreviewUrl(value.trim()) : null;

export type EditorMeta = {
  regions: string[];
  region_refs?: Record<string, string>;
  region_screens?: Record<string, string>;
  /** Regions with `has_red_dot: true` in area (valid `isRedDot` targets). */
  region_red_dot?: string[];
  fsm_nodes: string[];
  exec_names: string[];
  scenario_keys: string[];
};

function labelingHrefForRegion(meta: EditorMeta, value: string): string | null {
  const name = value.trim();
  if (!name) return null;
  const ref = meta.region_refs?.[name];
  const params = new URLSearchParams();
  if (ref) params.set("ref", ref);
  params.set("region", name);
  return `/labeling?${params.toString()}`;
}

type Props = {
  step: ScenarioStep;
  path: number[];
  meta: EditorMeta;
  onChange: (step: ScenarioStep) => void;
};

function setCond(step: ScenarioStep, cond: string, stype: string) {
  const trimmed = cond.trim();
  if (trimmed) step.cond = trimmed;
  else if ("cond" in step && stype !== "cond") delete step.cond;
}

/** Edits one step's own fields — inner steps of containers are nodes on the
 *  flow canvas, never nested forms. */
export function StepCard({ step, path, meta, onChange }: Props) {
  const stype = detectStepType(step);
  const pk = path.join("/");
  const regionLabelingHref = (v: string) => labelingHrefForRegion(meta, v);

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
          previewUrl={regionPreview}
          labelingHref={regionLabelingHref}
          warnIfUnknown
        />
      )}

      {stype === "long_click" && (
        <div className="form-grid-2">
          <SelectWithFreetext
            label="region (long_click)"
            value={String(step.long_click ?? "")}
            options={meta.regions}
            onChange={(v) => update({ long_click: v })}
            previewUrl={regionPreview}
            labelingHref={regionLabelingHref}
            warnIfUnknown
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
            previewUrl={regionPreview}
            labelingHref={regionLabelingHref}
            warnIfUnknown
          />
          <MatchParams step={step} onChange={onChange} pk={pk} />
        </>
      )}

      {stype === "while_match" && (
        <WhileMatchBlock step={step} meta={meta} onChange={onChange} pk={pk} />
      )}

      {stype === "while_scroll" && (
        <WhileScrollBlock step={step} meta={meta} onChange={onChange} pk={pk} />
      )}

      {stype === "group" && (
        <div>
          <p className="muted">Inline group — inner steps run in place (YAML anchor helper).</p>
          <InnerStepsNote count={Array.isArray(step.steps) ? step.steps.length : 0} />
        </div>
      )}

      {["wait_screen", "swipe", "tap", "ttl", "system_back"].includes(stype) && (
        <p className="muted">
          No form editor for <code>{stype}</code> yet — edit this step in the YAML tab.
        </p>
      )}

      {stype === "ocr" && (
        <SelectWithFreetext
          label="region (ocr)"
          value={String(step.ocr ?? "")}
          options={meta.regions}
          onChange={(v) => update({ ocr: v })}
          previewUrl={regionPreview}
          labelingHref={regionLabelingHref}
          warnIfUnknown
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
        <LoopBlock step={step} stype={stype} onChange={onChange} pk={pk} />
      )}

      {stype === "cond" && (
        <div>
          <p className="muted">Composite cond block — inner steps run only if guard above is true.</p>
          <InnerStepsNote count={Array.isArray(step.steps) ? step.steps.length : 0} />
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

function InnerStepsNote({ count }: { count: number }) {
  return (
    <p className="muted">
      Inner steps ({count}) are shown as nodes on the canvas — select them there.
    </p>
  );
}

function WhileMatchBlock({
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
  const inner = Array.isArray(step.steps) ? (step.steps as ScenarioStep[]) : [];
  const sat = Number(step.min_match_saturation ?? 0);
  return (
    <>
      <SelectWithFreetext
        label="region (while_match)"
        value={String(step.while_match ?? "")}
        options={meta.regions}
        onChange={(v) => onChange({ ...step, while_match: v })}
        previewUrl={regionPreview}
        labelingHref={(v) => labelingHrefForRegion(meta, v)}
        warnIfUnknown
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
      <InnerStepsNote count={inner.length} />
    </>
  );
}

function WhileScrollBlock({
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
  const inner = Array.isArray(step.steps) ? (step.steps as ScenarioStep[]) : [];
  const dir = String(step.direction ?? "up");
  return (
    <>
      <SelectWithFreetext
        label="region (while_scroll)"
        value={String(step.while_scroll ?? "")}
        options={meta.regions}
        onChange={(v) => onChange({ ...step, while_scroll: v })}
        previewUrl={regionPreview}
        labelingHref={(v) => labelingHrefForRegion(meta, v)}
        warnIfUnknown
      />
      <div className="form-grid-3" key={pk}>
        <label className="field-row">
          <span>direction</span>
          <AppListbox
            value={SWIPE_DIRECTIONS.includes(dir as (typeof SWIPE_DIRECTIONS)[number]) ? dir : "up"}
            onChange={(v) => onChange({ ...step, direction: v })}
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
            value={Number(step.delta ?? 400)}
            onChange={(e) =>
              onChange({ ...step, delta: parseInt(e.target.value, 10) || 400 })
            }
          />
        </label>
        <label className="field-row">
          <span>max iterations</span>
          <input
            type="number"
            min={0}
            max={999}
            value={Number(step.max ?? 6)}
            onChange={(e) => onChange({ ...step, max: parseInt(e.target.value, 10) || 0 })}
          />
        </label>
      </div>
      <InnerStepsNote count={inner.length} />
    </>
  );
}

function LoopBlock({
  step,
  stype,
  onChange,
  pk,
}: {
  step: ScenarioStep;
  stype: "loop" | "repeat";
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
      <InnerStepsNote count={inner.length} />
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
