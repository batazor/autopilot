"use client";

import { Field, Label, Switch } from "@headlessui/react";
import type { ReactNode } from "react";

export type AppSwitchProps = {
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

export function AppSwitch({
  checked,
  onChange,
  label,
  disabled = false,
  title,
  inline = false,
  className = "",
  fieldClassName = "",
  "aria-label": ariaLabel,
}: AppSwitchProps) {
  const control = (
    <Switch
      checked={checked}
      onChange={onChange}
      disabled={disabled}
      className="headless-switch"
      aria-label={ariaLabel ?? (typeof label === "string" ? label : undefined)}
      title={title}
    >
      <span aria-hidden className="headless-switch__thumb" />
    </Switch>
  );

  if (!label) {
    return <span className={className}>{control}</span>;
  }

  return (
    <Field
      disabled={disabled}
      className={`headless-switch-field${inline ? " headless-switch-field--inline" : ""} ${fieldClassName} ${className}`.trim()}
      title={title}
    >
      {control}
      <Label className="headless-switch-field__label">{label}</Label>
    </Field>
  );
}
