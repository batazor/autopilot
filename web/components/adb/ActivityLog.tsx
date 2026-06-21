import { Button, Icon } from "@/components/ui";
import type { AdbState } from "./useAdbState";

export function ActivityLog({ adb }: { adb: AdbState }) {
  const { activity, setActivity, activityLogText, shownActivityLog, activityCopied, copyActivityLog } =
    adb;

  return (
    <section className="panel panel--spaced">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="m-0 text-base font-semibold text-wos-text">Activity log</h2>
        <span className="text-xs text-wos-text-muted">
          {activity.length ? `${activity.length} event${activity.length === 1 ? "" : "s"}` : "waiting"}
        </span>
        <Button
          className="ml-auto inline-flex items-center gap-1 px-2 py-1 text-xs"
          disabled={!activityLogText}
          onClick={copyActivityLog}
        >
          <Icon name="copy" size="sm" />
          {activityCopied ? "Copied" : "Copy logs"}
        </Button>
        <Button
          className="inline-flex items-center gap-1 px-2 py-1 text-xs"
          disabled={!activity.length}
          onClick={() => setActivity([])}
        >
          <Icon name="trash" size="sm" />
          Clear
        </Button>
      </div>
      <pre className="mt-3 max-h-56 overflow-auto rounded-md bg-wos-surface p-2 font-mono text-xs leading-relaxed text-wos-text-secondary">
        {shownActivityLog}
      </pre>
    </section>
  );
}
