import type { ClickApprovalView } from "@/lib/types";

export function TaskContextCaption({
  ctx,
}: {
  ctx: NonNullable<ClickApprovalView["task_context"]>;
}) {
  const { threshold, score, text, confidence } = ctx;
  // Two flavours: overlay-by-text (OCR-driven) and overlay-by-template
  // (score-driven). Match the Streamlit caption exactly so logs and screenshots
  // can be compared 1:1.
  if (text) {
    return (
      <p className="meta">
        Overlay(text) · text <code>{text}</code>
        {confidence ? (
          <>
            {" "}
            · conf <code>{confidence}</code>
          </>
        ) : null}
      </p>
    );
  }
  if (threshold || score) {
    const parts: string[] = [];
    if (threshold) parts.push(`threshold ${threshold}`);
    if (score) parts.push(`match score ${score}`);
    return (
      <p className="meta">
        Overlay ·{" "}
        {parts.map((p, i) => (
          <span key={p}>
            <code>{p}</code>
            {i < parts.length - 1 ? " · " : ""}
          </span>
        ))}
      </p>
    );
  }
  return null;
}
