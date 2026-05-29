export function ErrorBanner({
  message,
  onRetry,
  retrying = false,
}: {
  message?: string | null;
  /** When provided, render a Retry button that re-runs the failed fetch. */
  onRetry?: () => void;
  retrying?: boolean;
}) {
  if (!message) return null;
  return (
    <div className="error-banner" role="alert">
      <span className="error-banner__message">{message}</span>
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
