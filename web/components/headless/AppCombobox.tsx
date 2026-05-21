"use client";

import {
  Combobox,
  ComboboxButton,
  ComboboxInput,
  ComboboxOption,
  ComboboxOptions,
} from "@headlessui/react";
import { useMemo, useState } from "react";
import type { SelectOption } from "@/components/AppSelect";

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      className={`h-4 w-4 shrink-0 text-wos-text-muted transition ${open ? "rotate-180" : ""}`}
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden
    >
      <path
        fillRule="evenodd"
        d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.24 4.5a.75.75 0 01-1.08 0l-4.24-4.5a.75.75 0 01.02-1.06z"
        clipRule="evenodd"
      />
    </svg>
  );
}

export type AppComboboxProps = {
  label?: string;
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  loading?: boolean;
  minWidth?: number;
  maxWidth?: number;
  inline?: boolean;
  fullWidth?: boolean;
  className?: string;
  title?: string;
  "aria-label"?: string;
  /** Optional custom matcher; defaults to case-insensitive substring on label + value. */
  filter?: (option: SelectOption, query: string) => boolean;
};

function defaultFilter(option: SelectOption, query: string) {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return (
    option.label.toLowerCase().includes(needle) ||
    option.value.toLowerCase().includes(needle)
  );
}

export function AppCombobox({
  label,
  options,
  value,
  onChange,
  placeholder = "Select…",
  disabled = false,
  loading = false,
  minWidth = 220,
  maxWidth,
  inline = false,
  fullWidth = false,
  className = "",
  title,
  "aria-label": ariaLabel,
  filter = defaultFilter,
}: AppComboboxProps) {
  const isDisabled = disabled || loading;
  const [query, setQuery] = useState("");

  const selected = !loading ? options.find((o) => o.value === value) : undefined;
  const displayValue = (val: unknown) => {
    const v = typeof val === "string" ? val : "";
    return options.find((o) => o.value === v)?.label ?? v;
  };

  const filteredOptions = useMemo(() => {
    if (!query.trim()) return options;
    return options.filter((opt) => filter(opt, query));
  }, [options, query, filter]);

  const control = (
    <Combobox
      value={value}
      onChange={(v: string | null) => onChange(v ?? "")}
      disabled={isDisabled}
    >
      {({ open }) => (
        <div
          className={`headless-listbox${fullWidth ? " headless-listbox--full" : ""}`}
          style={fullWidth ? { maxWidth } : { minWidth, maxWidth }}
        >
          <div className="headless-combobox__field">
            <ComboboxInput
              className="headless-combobox__input"
              aria-label={ariaLabel ?? label}
              title={title ?? selected?.label}
              displayValue={displayValue}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={loading ? "Loading…" : placeholder}
            />
            <ComboboxButton className="headless-combobox__button" aria-label="Toggle options">
              <ChevronIcon open={open} />
            </ComboboxButton>
          </div>
          <ComboboxOptions
            anchor="bottom start"
            transition
            className="headless-listbox__options"
          >
            {filteredOptions.length === 0 ? (
              <div className="headless-listbox__empty">
                {loading ? "Loading…" : "No matches"}
              </div>
            ) : (
              filteredOptions.map((opt) => (
                <ComboboxOption
                  key={opt.value || "__empty__"}
                  value={opt.value}
                  className="headless-listbox__option"
                >
                  {opt.label}
                </ComboboxOption>
              ))
            )}
          </ComboboxOptions>
        </div>
      )}
    </Combobox>
  );

  if (!label) return control;

  if (inline) {
    return (
      <label className={`headless-listbox-field headless-listbox-field--inline ${className}`}>
        <span>{label}</span>
        {control}
      </label>
    );
  }

  if (fullWidth) {
    return (
      <label className={`headless-listbox-field headless-listbox-field--block ${className}`}>
        <span>{label}</span>
        {control}
      </label>
    );
  }

  return (
    <label className={`app-select-field ${className}`}>
      <span>{label}</span>
      {control}
    </label>
  );
}
