"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "danger" | "success";

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary: "btn-primary",
  secondary: "btn-secondary",
  danger: "btn-danger",
  success: "btn-success",
};

type ButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> & {
  /** Visual style; maps to the shared `.btn-*` classes. */
  variant?: ButtonVariant;
  /**
   * Async work in flight. Disables the button and surfaces a spinner +
   * `data-pending` attribute so optimistic handlers never re-implement the busy
   * guard (per the Next.js interactive-apps guide — style pending in CSS).
   */
  pending?: boolean;
  /** Extra classes appended after the variant class. */
  className?: string;
  children: ReactNode;
};

/**
 * Shared button primitive wrapping the `.btn-*` design-system classes. Pass
 * `pending` for in-flight async actions; it blocks clicks and renders an inline
 * spinner.
 */
export function Button({
  variant = "secondary",
  pending = false,
  disabled,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={[VARIANT_CLASS[variant], pending ? "pending-btn" : "", className]
        .filter(Boolean)
        .join(" ")}
      data-pending={pending ? "" : undefined}
      aria-busy={pending || undefined}
      disabled={disabled || pending}
      {...rest}
    >
      {pending ? <Spinner size="sm" className="pending-btn__spinner" /> : null}
      {children}
    </button>
  );
}
