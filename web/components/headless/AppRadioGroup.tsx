"use client";

import { Radio, RadioGroup } from "@headlessui/react";
import type { ReactNode } from "react";

export type AppRadioOption = {
  value: string;
  label: ReactNode;
  /** Tooltip surfaced via the native title attribute. */
  title?: string;
  disabled?: boolean;
};

export type AppRadioGroupProps = {
  options: AppRadioOption[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  /** Visual layout. ``segmented`` = single pill bar (default, compact);
   *  ``stack`` = vertical list with leading radio dot. */
  variant?: "segmented" | "stack";
  className?: string;
  "aria-label"?: string;
};

export function AppRadioGroup({
  options,
  value,
  onChange,
  disabled = false,
  variant = "segmented",
  className = "",
  "aria-label": ariaLabel,
}: AppRadioGroupProps) {
  const containerClass =
    variant === "segmented"
      ? "headless-radio-group headless-radio-group--segmented"
      : "headless-radio-group headless-radio-group--stack";
  const itemClass =
    variant === "segmented" ? "headless-radio--pill" : "headless-radio--row";

  return (
    <RadioGroup
      value={value}
      onChange={onChange}
      disabled={disabled}
      aria-label={ariaLabel}
      className={`${containerClass} ${className}`.trim()}
    >
      {options.map((opt) => (
        <Radio
          key={opt.value || "__empty__"}
          value={opt.value}
          disabled={opt.disabled}
          title={opt.title}
          className={itemClass}
        >
          {variant === "stack" && (
            <span className="headless-radio__dot" aria-hidden />
          )}
          <span className="headless-radio__label">{opt.label}</span>
        </Radio>
      ))}
    </RadioGroup>
  );
}
