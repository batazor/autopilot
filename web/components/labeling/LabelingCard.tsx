"use client";

export function LabelingCard({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={["labeling-card", className].filter(Boolean).join(" ")}>
      {children}
    </section>
  );
}
