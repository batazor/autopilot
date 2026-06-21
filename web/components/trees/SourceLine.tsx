export function SourceLine({ url, label }: { url: string; label: string }) {
  return (
    <p className="muted mb-3 text-sm">
      Sourced from{" "}
      <a className="underline" href={url} target="_blank" rel="noreferrer">
        {label}
      </a>
      .
    </p>
  );
}
