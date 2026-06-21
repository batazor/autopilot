import { Pill, type PillTone } from "@/components/ui";

function toneFor(status: string): PillTone {
  if (status === "registered" || status === "bound") return "ok";
  if (status === "failed") return "danger";
  return "pending";
}

/** Account-status pill (registered/bound → ok, failed → danger, else pending). */
export function StatusBadge({ status }: { status: string }) {
  return (
    <Pill tone={toneFor(status)} dot>
      {status}
    </Pill>
  );
}
