import { Button, Card, Icon } from "@/components/ui";
import type { Pending, RegistrationStatus } from "@/lib/farm/types";
import { AutomationChip } from "./AutomationChip";

type RegistrationPanelProps = {
  pending: Pending;
  registrationStatus: RegistrationStatus | null;
  registrationRunning: boolean;
  busy: boolean;
  logsCopied: boolean;
  onFailed: () => void;
  onCopyLogs: () => void;
  onClearLogs: () => void;
};

/** "Beta registration" handoff panel: automation chips, current character, logs. */
export function RegistrationPanel({
  pending,
  registrationStatus,
  registrationRunning,
  busy,
  logsCopied,
  onFailed,
  onCopyLogs,
  onClearLogs,
}: RegistrationPanelProps) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="m-0 text-base font-semibold text-wos-text">Beta registration</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            <AutomationChip label="Image code" value={pending?.image_code} />
            <AutomationChip label="Slider" value={pending?.slider} />
            <AutomationChip label="Stage" value={pending?.stage} />
            <AutomationChip
              label="Attempt"
              value={
                pending?.register_attempt && pending?.register_max_attempts
                  ? `${pending.register_attempt}/${pending.register_max_attempts}`
                  : pending?.register_attempt
              }
            />
          </div>
        </div>
      </div>
      {pending ? (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-wos-border-subtle bg-wos-panel-raised/40 p-3">
          <div className="min-w-0 flex-1">
            <div className="text-xs font-semibold uppercase tracking-wide text-wos-text-muted">
              Current character
            </div>
            <div className="mt-0.5 truncate text-lg font-semibold text-wos-text">
              {pending.username}
            </div>
            <div className="mt-1 text-sm text-wos-text-secondary">
              Click Sign Up in the browser. The API response is detected automatically.
            </div>
            {pending.previous_register ? (
              <div className="mt-1 truncate text-xs text-amber-200">
                Previous response: {pending.previous_register}
              </div>
            ) : null}
          </div>
          <div className="flex gap-2">
            <Button disabled={busy} onClick={onFailed}>
              Failed
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-4 rounded-lg border border-wos-border-subtle bg-wos-panel-raised/30 px-3 py-2 text-sm text-wos-text-secondary">
          Ready for the next beta character.
        </div>
      )}
      {registrationStatus?.log_path || registrationStatus?.log_tail ? (
        <div className="mt-4 rounded-lg border border-wos-border-subtle bg-wos-panel-raised/30 p-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-wos-text-muted">
            <span>pid {registrationStatus.pid ?? "—"}</span>
            <span>exit {registrationStatus.exit_code ?? "—"}</span>
            <code className="max-w-full truncate rounded bg-wos-surface px-1.5 py-0.5">
              {registrationStatus.log_path ?? "log pending"}
            </code>
            <Button
              className="ml-auto inline-flex items-center gap-1 px-2 py-1 text-xs"
              disabled={!registrationStatus.log_tail}
              onClick={onCopyLogs}
            >
              <Icon name="copy" size="sm" />
              {logsCopied ? "Copied" : "Copy logs"}
            </Button>
            <Button
              className="inline-flex items-center gap-1 px-2 py-1 text-xs text-red-200"
              disabled={busy || registrationRunning || Boolean(pending)}
              onClick={onClearLogs}
              title={
                pending || registrationRunning
                  ? "Registration is still active"
                  : "Clear registration log"
              }
            >
              <Icon name="trash" size="sm" />
              Clear logs
            </Button>
          </div>
          {registrationStatus.log_tail ? (
            <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-wos-surface p-2 text-xs leading-relaxed text-wos-text-secondary">
              {registrationStatus.log_tail}
            </pre>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
