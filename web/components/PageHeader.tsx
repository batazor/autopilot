export function PageHeader({
  title,
  actions,
  children,
}: {
  title: string;
  /** Inline content (e.g. status pills) rendered on the same row as the title. */
  actions?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <header className="app-header">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h1>{title}</h1>
          {actions}
        </div>
        {children ? (
          <div className="mt-1 max-w-4xl text-sm text-wos-text-secondary">{children}</div>
        ) : null}
      </div>
    </header>
  );
}
