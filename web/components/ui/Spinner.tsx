import type { IconSize } from "@/components/ui/Icon";

type SpinnerProps = {
  size?: IconSize;
  className?: string;
  label?: string;
};

export function Spinner({
  size = "md",
  className = "",
  label = "Loading",
}: SpinnerProps) {
  return (
    <span
      className={["ui-spinner", className].filter(Boolean).join(" ")}
      role="status"
      aria-live="polite"
      aria-label={label}
    >
      <svg
        className={`ui-icon ui-icon--${size} ui-spinner__svg`}
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden
      >
        <circle
          className="ui-spinner__track"
          cx="12"
          cy="12"
          r="9"
          stroke="currentColor"
          strokeWidth="2"
        />
        <path
          className="ui-spinner__arc"
          d="M12 3a9 9 0 0 1 9 9"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}

/** Centered loading row for Suspense and panel placeholders. */
export function PageLoading({ message = "Loading…" }: { message?: string }) {
  return (
    <div className="ui-page-loading">
      <Spinner />
      <span className="ui-page-loading__text">{message}</span>
    </div>
  );
}
