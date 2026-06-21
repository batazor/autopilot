import { CopyButton } from "@/components/CopyButton";

export function ErrorBanner({
  message,
  copyReport,
  onRetry,
  retrying = false,
}: {
  message?: string | null;
  copyReport?: string | null;
  /** When provided, render a Retry button that re-runs the failed fetch. */
  onRetry?: () => void;
  retrying?: boolean;
}) {
  if (!message) return null;
  return (
    <div className="error-banner" role="alert">
      <span className="error-banner__message">{message}</span>
      {copyReport ? (
        <CopyButton
          text={copyReport}
          label="Copy report"
          title="Copy error report JSON"
          className="error-banner__copy"
        />
      ) : null}
      {onRetry ? (
        <button
          type="button"
          className="error-banner__retry"
          onClick={onRetry}
          disabled={retrying}
          aria-busy={retrying}
        >
          {retrying ? "Retrying…" : "Retry"}
        </button>
      ) : null}
    </div>
  );
}
