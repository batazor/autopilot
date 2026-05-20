"use client";

import { AppListbox } from "@/components/headless";
import type { LabelingScopeOption } from "@/lib/types";

type Props = {
  scopes: LabelingScopeOption[];
  scope: string;
  onChange: (key: string) => void;
  busy?: boolean;
};

export function LabelingModuleSelect({ scopes, scope, onChange, busy }: Props) {
  const active = scopes.find((s) => s.key === scope) ?? scopes[0];
  return (
    <AppListbox
      inline
      className="labeling-module-select meta"
      label="Module"
      value={scope}
      onChange={onChange}
      disabled={busy || scopes.length === 0}
      options={scopes.map((s) => ({ value: s.key, label: s.label }))}
      minWidth={200}
      title={
        active
          ? `${active.references_prefix} · ${active.area_path}`
          : "Scope screenshots and area layout"
      }
    />
  );
}
