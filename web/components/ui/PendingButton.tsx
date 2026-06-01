"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";

type Variant = "primary" | "secondary" | "danger" | "success";

const VARIANT_CLASS: Record<Variant, string> = {
  primary: "btn-primary",
  secondary: "btn-secondary",
  danger: "btn-danger",
  success: "btn-success",
};

type PendingButtonProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "className"
> & {
  /** Visual style; maps to the shared .btn-* classes. */
  variant?: Variant;
  /** Async work in flight. Disables the button and surfaces a spinner + data-pending. */
  pending?: boolean;
  /** Extra classes appended after the variant class. */
  className?: string;
  children: ReactNode;
};

/**
 * Shared button that exposes its busy state via a `data-pending` attribute
 * (per the Next.js interactive-apps guide — style pending states in CSS rather
 * than lifting state). Renders an inline spinner and blocks clicks while
 * `pending`, so optimistic handlers never need to re-implement the busy guard.
 */
export function PendingButton({
  variant = "secondary",
  pending = false,
  disabled,
  className,
  children,
  type = "button",
  ...rest
}: PendingButtonProps) {
  return (
    <button
      type={type}
      className={[VARIANT_CLASS[variant], "pending-btn", className]
        .filter(Boolean)
        .join(" ")}
      data-pending={pending ? "" : undefined}
      aria-busy={pending || undefined}
      disabled={disabled || pending}
      {...rest}
    >
      {pending ? (
        <Spinner size="sm" className="pending-btn__spinner" />
      ) : null}
      {children}
    </button>
  );
}
