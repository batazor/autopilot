export function ErrorBanner({ message }: { message?: string | null }) {
  if (!message) return null;
  return (
    <div className="error-banner" role="alert">
      {message}
    </div>
  );
}
