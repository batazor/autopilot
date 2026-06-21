"use client";

import Select, { type SingleValue, type StylesConfig } from "react-select";
import { AppListbox } from "@/components/headless/AppListbox";
import { selectThemeStyles } from "@/lib/select-theme-styles";

export type SelectOption = {
  value: string;
  label: string;
};

type Props = {
  label: string;
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  /** While the option list is still fetching. */
  loading?: boolean;
  minWidth?: number;
  maxWidth?: number;
  isSearchable?: boolean;
};

export function AppSelect({
  label,
  options,
  value,
  onChange,
  placeholder,
  disabled = false,
  loading = false,
  minWidth = 180,
  maxWidth,
  isSearchable = true,
}: Props) {
  const resolvedPlaceholder = placeholder ?? (loading ? "Loading…" : "Select…");
  const isDisabled = disabled || loading;

  if (!isSearchable) {
    return (
      <AppListbox
        label={label}
        options={options}
        value={value}
        onChange={onChange}
        placeholder={resolvedPlaceholder}
        disabled={isDisabled}
        loading={loading}
        minWidth={minWidth}
        maxWidth={maxWidth}
      />
    );
  }

  const selected =
    !loading && value
      ? (options.find((option) => option.value === value) ?? null)
      : null;
  const styles: StylesConfig<SelectOption, false> = {
    ...selectThemeStyles,
    container: (base) => ({
      ...base,
      minWidth,
      maxWidth,
    }),
  };

  return (
    <label className="app-select-field">
      <span>{label}</span>
      <Select<SelectOption, false>
        className="app-select"
        classNamePrefix="app-select"
        styles={styles}
        options={options}
        value={selected}
        onChange={(next: SingleValue<SelectOption>) => {
          if (next) onChange(next.value);
        }}
        placeholder={resolvedPlaceholder}
        isDisabled={isDisabled}
        isSearchable={isSearchable}
        menuShouldScrollIntoView={false}
        noOptionsMessage={() => "No options"}
        instanceId={`select-${label.replace(/\s+/g, "-").toLowerCase()}`}
        aria-label={label}
        theme={(theme) => ({
          ...theme,
          colors: {
            ...theme.colors,
            primary: "var(--wos-accent)",
            primary25: "var(--wos-option-hover)",
            neutral0: "var(--wos-panel)",
            neutral80: "var(--wos-text)",
          },
        })}
      />
    </label>
  );
}
