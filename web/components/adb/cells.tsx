import type { AdbDetectedGame, ScrcpyStatus } from "@/lib/config-pages";
import { type CellEntry, gameBadgeLabel } from "@/lib/adb/types";

/** Scrcpy install status cell (checking / error / installed / not installed). */
export function ScrcpyStatusCell({ entry }: { entry: CellEntry<ScrcpyStatus> }) {
  if (entry === undefined) return <span className="muted">checking…</span>;
  if ("error" in entry) {
    return (
      <span className="error-text" title={entry.error}>
        error
      </span>
    );
  }
  const detail = [entry.abi, entry.sdk ? `android-${entry.sdk}` : null]
    .filter(Boolean)
    .join(" · ");
  if (entry.installed) {
    return <span className="success-text">installed{detail && ` · ${detail}`}</span>;
  }
  return (
    <span className="muted" title={entry.last_error ?? "missing"}>
      not installed{detail && ` · ${detail}`}
    </span>
  );
}

/** Detected-game pills for a live device. */
export function DetectedGames({ games }: { games?: AdbDetectedGame[] }) {
  if (!games?.length) return <span className="muted">—</span>;
  return (
    <span className="flex flex-wrap gap-1.5">
      {games.map((game) => (
        <span
          key={`${game.id}-${game.package}`}
          className={`status-pill ${game.running ? "pill-live" : "pill-busy"}`}
          title={`${game.label} (${game.package}) · ${game.running ? "running" : "installed"}`}
        >
          <span>{gameBadgeLabel(game)}</span>
          {game.beta && (
            <span className="rounded-full border border-current/40 px-1 py-0 text-[9px] font-semibold uppercase opacity-90">
              beta
            </span>
          )}
        </span>
      ))}
    </span>
  );
}
