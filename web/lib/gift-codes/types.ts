import type { ExternalAccountsGame } from "@/components/gift-codes/ExternalAccountsPanel";

export const KNOWN_GAMES: ExternalAccountsGame[] = [
  { id: "wos", label: "Whiteout Survival" },
  { id: "kingshot", label: "Kingshot" },
  { id: "wos_beta", label: "WOS Beta" },
  { id: "kingshot_beta", label: "Kingshot Beta" },
];

export const DEFAULT_GAME = KNOWN_GAMES[0]?.id ?? "wos";
export const EXTERNAL_ACCOUNT_GAME_IDS = new Set(["wos", "kingshot"]);
export const BETA_GIFT_CODE_GAME_IDS = new Set(["wos_beta", "kingshot_beta"]);
export const EXTERNAL_ACCOUNT_GAMES = KNOWN_GAMES.filter((g) =>
  EXTERNAL_ACCOUNT_GAME_IDS.has(g.id),
);

export const INPUT_CLASS = "field";
export const LABEL_CLASS = "text-xs font-medium uppercase tracking-wide text-wos-text-muted";

export const STATUS_CLASS: Record<string, string> = {
  PENDING: "pill-paused",
  SUCCESS: "pill-live",
  ALREADY_RECEIVED: "pill-live",
  CDK_EXPIRED: "pill-offline",
  CDK_NOT_FOUND: "pill-offline",
  STOVE_LEVEL_TOO_LOW: "pill-danger",
  VIP_LEVEL_TOO_LOW: "pill-danger",
  FAILED: "pill-danger",
};

// Hover help shown on each status pill so operators can read what a state
// means without cross-referencing the err_code table.
export const STATUS_HELP: Record<string, string> = {
  PENDING: "Queued — not attempted yet.",
  SUCCESS: "Redeemed successfully.",
  ALREADY_RECEIVED: "This account already claimed this code.",
  CDK_EXPIRED: "The code has expired.",
  CDK_NOT_FOUND: "The game server doesn't recognize this code.",
  STOVE_LEVEL_TOO_LOW: "Furnace / Town Center level too low for this code.",
  VIP_LEVEL_TOO_LOW: "Account VIP level too low for this code.",
  FAILED:
    "Redeem failed — often transient (e.g. Kingshot login/session expired, err_code 40009). Retried on the next run.",
};

// Compact labels so the per-player status column doesn't overflow the table;
// the full meaning stays in the hover tooltip (STATUS_HELP + nickname).
export const STATUS_SHORT: Record<string, string> = {
  ALREADY_RECEIVED: "RECEIVED",
  CDK_EXPIRED: "EXPIRED",
  CDK_NOT_FOUND: "NOT FOUND",
  STOVE_LEVEL_TOO_LOW: "STOVE LOW",
  VIP_LEVEL_TOO_LOW: "VIP LOW",
};

export function formatDuration(totalSeconds: number): string {
  const total = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
