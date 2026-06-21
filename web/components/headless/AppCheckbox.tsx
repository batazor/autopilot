"use client";

import { Checkbox, Field, Label } from "@headlessui/react";
import type { ReactNode } from "react";

function CheckIcon() {
  return (
    <svg
      className="headless-checkbox__icon"
      viewBox="0 0 14 14"
      fill="none"
      aria-hidden
    >
      <path
        d="M3 8L6 11L11 3.5"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export type AppCheckboxProps = {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: ReactNode;
  disabled?: boolean;
  title?: string;
  /** Toolbar-style: label and control on one row */
  inline?: boolean;
  className?: string;
  fieldClassName?: string;
  "aria-label"?: string;
};

export function AppCheckbox({
  checked,
  onChange,
  label,
  disabled = false,
  title,
  inline = false,
  className = "",
  fieldClassName = "",
  "aria-label": ariaLabel,
}: AppCheckboxProps) {
  const control = (
    <Checkbox
      checked={checked}
      onChange={onChange}
      disabled={disabled}
      className="headless-checkbox"
      aria-label={ariaLabel ?? (typeof label === "string" ? label : undefined)}
      title={title}
    >
      <CheckIcon />
    </Checkbox>
  );

  if (!label) {
    return <span className={className}>{control}</span>;
  }

  return (
    <Field
      disabled={disabled}
      className={`headless-checkbox-field${inline ? " headless-checkbox-field--inline" : ""} ${fieldClassName} ${className}`.trim()}
      title={title}
    >
      {control}
      <Label className="headless-checkbox-field__label">{label}</Label>
    </Field>
  );
}
