import { FleetHeaderBar, type FleetHeaderBarProps } from "@/components/FleetHeaderBar";

export function PageHeader({
  title,
  actions,
  children,
  fleet,
}: {
  title: string;
  /** Inline content (e.g. status pills) rendered on the same row as the title. */
  actions?: React.ReactNode;
  children?: React.ReactNode;
  /**
   * Render the fleet bar (instance/player selectors + quick-nav popover) on the
   * right. Pass `true` for defaults, or an options object to tweak it.
   */
  fleet?: boolean | FleetHeaderBarProps;
}) {
  const fleetProps = fleet === true ? {} : fleet || null;
  return (
    <header className="app-header">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1>{title}</h1>
          {actions}
        </div>
        {children ? (
          <div className="mt-1 max-w-4xl text-sm text-wos-text-secondary">{children}</div>
        ) : null}
      </div>
      {fleetProps ? <FleetHeaderBar {...fleetProps} /> : null}
    </header>
  );
}
