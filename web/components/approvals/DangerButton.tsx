import { Button } from "@/components/ui";

/** Two-step "arm, then confirm" button. Avoids the jarring native
 *  window.confirm() while still requiring a deliberate second click. */
export function DangerButton({
  label,
  confirmLabel,
  tooltip,
  confirming,
  busy,
  disabled,
  onArm,
  onCancel,
  onConfirm,
}: {
  label: string;
  confirmLabel: string;
  tooltip?: string;
  confirming: boolean;
  busy: boolean;
  disabled: boolean;
  onArm: () => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (busy) {
    return (
      <Button variant="danger" disabled>
        {confirmLabel.replace(/^Confirm /, "")}…
      </Button>
    );
  }
  if (confirming) {
    return (
      <span className="danger-confirm">
        <Button variant="danger" onClick={onConfirm}>
          {confirmLabel}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </span>
    );
  }
  return (
    <Button disabled={disabled} onClick={onArm} title={tooltip}>
      {label}
    </Button>
  );
}
