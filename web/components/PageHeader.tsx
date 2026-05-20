export function PageHeader({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="app-header">
      <div>
        <h1>{title}</h1>
        {children ? (
          <div className="mt-1 max-w-4xl text-sm text-wos-text-secondary">{children}</div>
        ) : null}
      </div>
    </header>
  );
}
