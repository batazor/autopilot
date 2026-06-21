"use client";

import {
  Listbox,
  ListboxButton,
  ListboxOption,
  ListboxOptions,
} from "@headlessui/react";
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

export type AppListboxProps = {
  label?: string;
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  /** While the option list is still fetching. */
  loading?: boolean;
  minWidth?: number;
  maxWidth?: number;
  /** Toolbar-style: label and control on one row */
  inline?: boolean;
  /** Stack label above control at full width (sidebar forms) */
  fullWidth?: boolean;
  className?: string;
  title?: string;
  "aria-label"?: string;
};

export function AppListbox({
  label,
  options,
  value,
  onChange,
  placeholder = "Select…",
  disabled = false,
  loading = false,
  minWidth = 180,
  maxWidth,
  inline = false,
  fullWidth = false,
  className = "",
  title,
  "aria-label": ariaLabel,
}: AppListboxProps) {
  const isDisabled = disabled || loading;
  const selected = !loading ? options.find((o) => o.value === value) : undefined;
  const display = loading ? placeholder : (selected?.label ?? placeholder);

  const control = (
    <Listbox value={value} onChange={onChange} disabled={isDisabled}>
      {({ open }) => (
        <div
          className={`headless-listbox${fullWidth ? " headless-listbox--full" : ""}`}
          style={fullWidth ? { maxWidth } : { minWidth, maxWidth }}
        >
          <ListboxButton
            className="headless-listbox__button"
            aria-label={ariaLabel ?? label}
            title={title}
          >
            <span className="headless-listbox__value">{display}</span>
            <ChevronIcon open={open} />
          </ListboxButton>
          <ListboxOptions
            anchor="bottom start"
            transition
            className="headless-listbox__options"
          >
            {options.length === 0 ? (
              <div className="headless-listbox__empty">
                {loading ? "Loading…" : "No options"}
              </div>
            ) : (
              options.map((opt) => (
                <ListboxOption
                  key={opt.value || "__empty__"}
                  value={opt.value}
                  className="headless-listbox__option"
                >
                  {opt.label}
                </ListboxOption>
              ))
            )}
          </ListboxOptions>
        </div>
      )}
    </Listbox>
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
