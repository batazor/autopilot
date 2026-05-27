"use client";

import { useMemo } from "react";
import { AppCombobox } from "@/components/headless";
import type { LabelingScopeOption } from "@/lib/types";

type Props = {
  scopes: LabelingScopeOption[];
  scope: string;
  onChange: (key: string) => void;
  busy?: boolean;
};

export function LabelingModuleCombobox({ scopes, scope, onChange, busy }: Props) {
  const active = scopes.find((s) => s.key === scope);
  const options = useMemo(
    () =>
      scopes
        .map((s) => ({ value: s.key, label: s.label }))
        .sort((a, b) => {
          if (a.value === "all") return -1;
          if (b.value === "all") return 1;
          return a.value.localeCompare(b.value, undefined, { sensitivity: "base" });
        }),
    [scopes],
  );

  return (
    <AppCombobox
      fullWidth
      label="Module (save scope)"
      value={scope}
      onChange={onChange}
      options={options}
      placeholder="Search module…"
      disabled={busy || scopes.length === 0}
      title={
        active
          ? `${active.references_prefix} · ${active.area_path}`
          : "Scope screenshots and area layout"
      }
    />
  );
}
