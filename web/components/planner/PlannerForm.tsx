"use client";

import { Button } from "@/components/ui";
import type { PlannerMeta } from "@/lib/api";
import { BuildingLevelsEditor } from "./BuildingLevelsEditor";
import type { PlannerDomainConfig } from "./domains";

type Props = {
  cfg: PlannerDomainConfig;
  values: Record<string, string>;
  meta: PlannerMeta | null;
  busy: boolean;
  onChange: (key: string, val: string) => void;
  onCompute: () => void;
  onReset: () => void;
};

export function PlannerForm({
  cfg,
  values,
  meta,
  busy,
  onChange,
  onCompute,
  onReset,
}: Props) {
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        onCompute();
      }}
    >
      {cfg.fields.map((f) => {
        const id = `pf-${cfg.id}-${f.key}`;
        const val = values[f.key] ?? "";

        if (f.kind === "bool") {
          return (
            <label key={f.key} htmlFor={id} className="flex items-center gap-2">
              <input
                id={id}
                type="checkbox"
                checked={val === "true"}
                onChange={(e) => onChange(f.key, e.target.checked ? "true" : "false")}
              />
              <span className="text-sm font-medium text-wos-text">{f.label}</span>
              {f.help ? (
                <span className="text-xs text-wos-text-muted">{f.help}</span>
              ) : null}
            </label>
          );
        }

        return (
          <div key={f.key} className="flex flex-col gap-1">
            <label htmlFor={id} className="text-sm font-medium text-wos-text">
              {f.label}
            </label>

            {f.kind === "building_levels" ? (
              <BuildingLevelsEditor
                value={val}
                onChange={(json) => onChange(f.key, json)}
              />
            ) : f.kind === "role" ? (
              <select
                id={id}
                className="field"
                value={val}
                onChange={(e) => onChange(f.key, e.target.value)}
              >
                <option value="">— default —</option>
                {(meta?.roles ?? []).map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            ) : f.kind === "json" ? (
              <textarea
                id={id}
                className="field font-mono text-xs"
                rows={Math.min(12, Math.max(3, val.split("\n").length + 1))}
                spellCheck={false}
                value={val}
                onChange={(e) => onChange(f.key, e.target.value)}
              />
            ) : (
              <input
                id={id}
                className="field"
                type={f.kind === "number" ? "number" : "text"}
                value={val}
                onChange={(e) => onChange(f.key, e.target.value)}
              />
            )}

            {f.help ? (
              <p className="text-xs text-wos-text-muted">{f.help}</p>
            ) : null}
          </div>
        );
      })}

      <div className="flex gap-2 pt-1">
        <Button type="submit" variant="primary" pending={busy}>
          Compute
        </Button>
        <Button type="button" variant="secondary" onClick={onReset} disabled={busy}>
          Reset
        </Button>
      </div>
    </form>
  );
}
