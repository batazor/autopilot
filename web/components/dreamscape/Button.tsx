"use client";

import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "accent" | "secondary";
type Size = "sm" | "md";

const BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-md font-medium shadow-sm transition focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50";

const VARIANTS: Record<Variant, string> = {
  // Green call-to-action (upload, create, …)
  primary:
    "bg-emerald-600 text-white hover:bg-emerald-500 focus:ring-emerald-500/50",
  // Themed accent (save / confirm)
  accent:
    "bg-wos-accent text-wos-on-accent hover:opacity-90 focus:ring-wos-accent/50",
  // Neutral filled (secondary actions)
  secondary:
    "border border-wos-border bg-wos-panel-raised text-wos-text hover:border-wos-border-hover focus:ring-wos-border-hover",
};

const SIZES: Record<Size, string> = {
  sm: "px-2.5 py-1 text-xs",
  md: "px-3 py-1.5 text-sm",
};

export function Button({
  variant = "secondary",
  size = "md",
  className = "",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
}) {
  return (
    <button
      type={type}
      className={`${BASE} ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...props}
    />
  );
}
