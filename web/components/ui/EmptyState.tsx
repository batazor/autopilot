import type { ReactNode } from "react";
import { Icon, type IconName } from "@/components/ui/Icon";

type EmptyStateProps = {
  title: string;
  description?: string;
  icon?: IconName;
  action?: ReactNode;
  className?: string;
};

export function EmptyState({
  title,
  description,
  icon = "inbox-empty",
  action,
  className = "",
}: EmptyStateProps) {
  return (
    <div className={["ui-empty", className].filter(Boolean).join(" ")}>
      <Icon name={icon} size="lg" className="ui-empty__icon" />
      <p className="ui-empty__title">{title}</p>
      {description ? <p className="ui-empty__desc">{description}</p> : null}
      {action ? <div className="ui-empty__action">{action}</div> : null}
    </div>
  );
}
