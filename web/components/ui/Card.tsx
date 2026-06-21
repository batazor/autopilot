import type { HTMLAttributes, ReactNode } from "react";

type CardProps = Omit<HTMLAttributes<HTMLDivElement>, "title"> & {
  /** Optional heading rendered as the panel's `<h2>`. */
  title?: ReactNode;
  /** Extra classes appended after the `.panel` base class. */
  className?: string;
  children: ReactNode;
};

/**
 * Surface primitive wrapping the shared `.panel` design-system class
 * (rounded, bordered, subtle shadow). Pass `title` for the standard `<h2>`.
 */
export function Card({ title, className, children, ...rest }: CardProps) {
  return (
    <div className={["panel", className].filter(Boolean).join(" ")} {...rest}>
      {title ? <h2>{title}</h2> : null}
      {children}
    </div>
  );
}
