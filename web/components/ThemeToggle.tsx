"use client";

import { useTheme } from "@/components/ThemeProvider";

type Props = {
  className?: string;
  /** Compact label for nav footer / mobile header. */
  compact?: boolean;
};

export function ThemeToggle({ className = "", compact = false }: Props) {
  const { theme, toggleTheme } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  const label =
    theme === "dark"
      ? "Switch to light theme (wiki / daytime)"
      : "Switch to dark theme (ops)";

  return (
    <button
      type="button"
      className={`theme-toggle ${className}`.trim()}
      onClick={toggleTheme}
      title={label}
      aria-label={label}
    >
      <span className="theme-toggle__icon" aria-hidden>
        {theme === "dark" ? "☀" : "☾"}
      </span>
      {compact ? (
        <span className="theme-toggle__label">{next}</span>
      ) : (
        <span className="theme-toggle__label">
          {theme === "dark" ? "Light theme" : "Dark theme"}
        </span>
      )}
    </button>
  );
}
