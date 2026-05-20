"use client";

import CreatableSelect from "react-select/creatable";
import type { SingleValue, StylesConfig } from "react-select";
import type { SelectOption } from "@/components/AppSelect";
import { selectThemeStyles } from "@/lib/select-theme-styles";

type Props = {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
};

export function SelectWithFreetext({
  label,
  value,
  options,
  onChange,
  disabled = false,
  placeholder,
}: Props) {
  const cur = (value || "").trim();
  const opts: SelectOption[] = [];
  const seen = new Set<string>();
  if (cur && !options.includes(cur)) {
    opts.push({ value: cur, label: cur });
    seen.add(cur);
  }
  for (const o of options) {
    if (o && !seen.has(o)) {
      opts.push({ value: o, label: o });
      seen.add(o);
    }
  }
  if (!opts.length && !cur) {
    opts.push({ value: "", label: "(empty)" });
  }

  const selected = opts.find((o) => o.value === cur) ?? (cur ? { value: cur, label: cur } : null);

  return (
    <label className="app-select-field">
      <span>{label}</span>
      <CreatableSelect<SelectOption, false>
        className="app-select"
        classNamePrefix="app-select"
        styles={{
          ...selectThemeStyles,
          container: (base) => ({ ...base, minWidth: 160 }),
        }}
        options={opts}
        value={selected}
        onChange={(next: SingleValue<SelectOption>) => onChange(next?.value ?? "")}
        onCreateOption={(input) => onChange(input.trim())}
        placeholder={placeholder ?? "Select or type…"}
        isDisabled={disabled}
        isClearable
        formatCreateLabel={(input) => `Use "${input}"`}
        menuShouldScrollIntoView={false}
        instanceId={`creatable-${label.replace(/\s+/g, "-").toLowerCase()}`}
      />
    </label>
  );
}
