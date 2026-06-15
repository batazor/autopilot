"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  ExternalAccountsPanel,
  type ExternalAccountsGame,
} from "@/components/gift-codes/ExternalAccountsPanel";
import { AppTabs } from "@/components/headless";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { PageHeader } from "@/components/PageHeader";
import {
  fetchBotStatus,
  fetchGiftCodeDiscordConfig,
  fetchGiftCodePollStatus,
  fetchGiftCodes,
  redeemGiftCodes,
  scrapeGiftCodes,
  updateGiftCodeDiscordConfig,
  type GiftCodeDiscordConfig,
} from "@/lib/api";
import type { GiftCodeRow } from "@/lib/wiki";

const KNOWN_GAMES: ExternalAccountsGame[] = [
  { id: "wos", label: "Whiteout Survival" },
  { id: "kingshot", label: "Kingshot" },
  { id: "wos_beta", label: "WOS Beta" },
  { id: "kingshot_beta", label: "Kingshot Beta" },
];

const DEFAULT_GAME = KNOWN_GAMES[0]?.id ?? "wos";
const EXTERNAL_ACCOUNT_GAME_IDS = new Set(["wos", "kingshot"]);
const BETA_GIFT_CODE_GAME_IDS = new Set(["wos_beta", "kingshot_beta"]);
const EXTERNAL_ACCOUNT_GAMES = KNOWN_GAMES.filter((g) =>
  EXTERNAL_ACCOUNT_GAME_IDS.has(g.id),
);
const INPUT_CLASS =
  "rounded-lg border border-wos-border-subtle bg-wos-input px-2.5 py-1.5 text-sm text-wos-text focus:border-sky-400/70 focus:outline-none focus:ring-2 focus:ring-sky-400/25";
const LABEL_CLASS = "text-xs font-medium uppercase tracking-wide text-wos-text-muted";

const STATUS_CLASS: Record<string, string> = {
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
const STATUS_HELP: Record<string, string> = {
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
const STATUS_SHORT: Record<string, string> = {
  ALREADY_RECEIVED: "RECEIVED",
  CDK_EXPIRED: "EXPIRED",
  CDK_NOT_FOUND: "NOT FOUND",
  STOVE_LEVEL_TOO_LOW: "STOVE LOW",
  VIP_LEVEL_TOO_LOW: "VIP LOW",
};

function CopyableCode({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      title="Copy code"
      aria-label={`Copy code ${code}`}
      className="group inline-flex cursor-pointer items-center gap-1.5 border-0 bg-transparent p-0 text-left"
    >
      <code>{code}</code>
      <span
        aria-hidden
        className={`text-xs ${copied ? "text-emerald-400" : "text-wos-text-muted opacity-0 transition-opacity group-hover:opacity-100"}`}
      >
        {copied ? "✓" : "⧉"}
      </span>
    </button>
  );
}

function GiftCodesTable({
  rows,
  playerIds,
  title,
}: {
  rows: GiftCodeRow[];
  playerIds: string[];
  title: string;
}) {
  if (!rows.length) return null;
  return (
    <section className="panel panel--spaced">
      <h2>{title}</h2>
      <div className="data-table-wrap">
        <table className="data-table gift-codes-table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Expires</th>
              <th>Expired</th>
              <th>Needs run</th>
              <th>API err</th>
              {playerIds.map((pid) => (
                <th key={pid}>{pid}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.code}
                className={r.slot_expired ? "gift-row-expired" : undefined}
              >
                <td>
                  <CopyableCode code={r.code} />
                </td>
                <td>{r.expires}</td>
                <td>{r.slot_expired ? "yes" : "no"}</td>
                <td>{r.needs_run ? "yes" : "no"}</td>
                <td>{r.api_err}</td>
                {playerIds.map((pid) => {
                  const p = r.players[pid];
                  const st = p?.status ?? "—";
                  const cls = STATUS_CLASS[st] ?? "pill-offline";
                  const help = STATUS_HELP[st];
                  const tip = [help, p?.label].filter(Boolean).join(" — ") || undefined;
                  const shortLabel = STATUS_SHORT[st] ?? st;
                  return (
                    <td key={pid}>
                      <span
                        className={`status-pill whitespace-nowrap ${cls}`}
                        title={tip}
                      >
                        {shortLabel}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function DiscordConfigPanel({
  config,
  token,
  busy,
  error,
  onTokenChange,
  onSave,
  onClearToken,
}: {
  config: GiftCodeDiscordConfig | null;
  token: string;
  busy: boolean;
  error: string | null;
  onTokenChange: (value: string) => void;
  onSave: () => void;
  onClearToken: () => void;
}) {
  const tokenMissing = !config?.token_configured;
  return (
    <section className="panel panel--spaced">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="m-0">Discord beta source</h2>
          <p className="muted m-0">
            <span
              className={`status-pill ${
                config?.token_configured ? "pill-live" : "pill-paused"
              }`}
            >
              {config?.token_configured ? "Token configured" : "Token missing"}
            </span>
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-secondary"
            disabled={busy || !config?.token_configured}
            onClick={onClearToken}
          >
            Clear token
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={onSave}
          >
            Save Discord
          </button>
        </div>
      </div>

      <ErrorBanner
        message={
          error ??
          (tokenMissing
            ? "A Discord token is required before beta gift codes can be scraped. Add a token below and click Save Discord."
            : null)
        }
      />

      <div className="grid gap-3 md:grid-cols-3">
        <label className="form-field">
          <span className={LABEL_CLASS}>Bot token</span>
          <input
            type="password"
            autoComplete="off"
            placeholder={config?.token_configured ? "saved" : "required"}
            value={token}
            onChange={(e) => onTokenChange(e.target.value)}
            className={INPUT_CLASS}
          />
          <span className="text-xs leading-snug text-wos-text-muted">
            A Discord bot token (Developer Portal) or a personal
            Authorization/user token both work. Note: using a user token with
            the Discord API is against Discord&rsquo;s ToS and can get the
            account flagged.
          </span>
        </label>
        <label className="form-field">
          <span className={LABEL_CLASS}>WOS Beta channel ID · built-in</span>
          <input
            inputMode="numeric"
            readOnly
            value={config?.wos_beta_channel_id ?? ""}
            className={INPUT_CLASS}
          />
        </label>
        <label className="form-field">
          <span className={LABEL_CLASS}>Kingshot Beta channel ID · built-in</span>
          <input
            inputMode="numeric"
            readOnly
            value={config?.kingshot_beta_channel_id ?? ""}
            className={INPUT_CLASS}
          />
        </label>
      </div>
    </section>
  );
}

function formatDuration(totalSeconds: number): string {
  const total = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

// Live countdown to the next scheduler-driven scrape for `game`. The backend
// returns the remaining TTL of the cadence key; we anchor it to an absolute
// target so a 1s tick stays accurate, and re-fetch every 30s (the scheduler
// tick cadence) to resync and pick up a reset after a cycle fires.
function NextPollTimer({ game }: { game: string }) {
  const [intervalSeconds, setIntervalSeconds] = useState<number | null>(null);
  const [targetMs, setTargetMs] = useState<number | null>(null);
  const [unknown, setUnknown] = useState(false);
  // The scheduler that drives auto-scrape runs inside the bot worker, so its
  // running state is what makes the countdown meaningful. null = unknown yet.
  const [schedulerRunning, setSchedulerRunning] = useState<boolean | null>(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const s = await fetchGiftCodePollStatus(game);
        if (!alive) return;
        setIntervalSeconds(s.interval_seconds);
        if (s.next_poll_seconds === null) {
          setUnknown(true);
          setTargetMs(null);
        } else {
          setUnknown(false);
          setTargetMs(Date.now() + s.next_poll_seconds * 1000);
        }
      } catch {
        if (!alive) return;
        setUnknown(true);
        setTargetMs(null);
      }
      try {
        const bot = await fetchBotStatus();
        if (alive) setSchedulerRunning(bot.running);
      } catch {
        if (alive) setSchedulerRunning(null);
      }
    };
    load();
    const refetch = setInterval(load, 30_000);
    const tick = setInterval(() => setTick((n) => n + 1), 1000);
    return () => {
      alive = false;
      clearInterval(refetch);
      clearInterval(tick);
    };
  }, [game]);

  const everyLabel =
    intervalSeconds !== null
      ? `Auto-scrape every ${Math.round(intervalSeconds / 3600)}h · `
      : "";

  let countdown: string;
  if (unknown) {
    countdown = "next run: unknown";
  } else if (targetMs === null) {
    countdown = "next run: due now";
  } else {
    const remaining = Math.max(0, (targetMs - Date.now()) / 1000);
    countdown =
      remaining < 1 ? "next run: due now" : `next run in ${formatDuration(remaining)}`;
  }

  return (
    <p className="mb-3 flex flex-wrap items-center gap-2 text-sm text-wos-text-muted">
      <span>
        {everyLabel}
        {countdown}
      </span>
      {schedulerRunning === false ? (
        <span
          className="status-pill pill-paused"
          title="The bot worker (which runs the scheduler) is stopped, so auto-scrape won't fire until it's started."
        >
          scheduler stopped
        </span>
      ) : schedulerRunning === true ? (
        <span className="status-pill pill-live" title="Scheduler is running.">
          scheduler active
        </span>
      ) : null}
    </p>
  );
}

function GiftCodesContent() {
  const params = useSearchParams();
  // Game lives in the URL (?game=…) so the selection is a shareable/bookmarkable
  // reference. Falls back to wos for missing/unknown values.
  const urlGameParam = params.get("game") ?? DEFAULT_GAME;
  const urlGame = KNOWN_GAMES.some((g) => g.id === urlGameParam)
    ? urlGameParam
    : DEFAULT_GAME;

  // Local state drives the UI synchronously. We mirror it to the URL via the
  // History API for shareable links — router.replace() is unreliable for
  // query-only changes in the App Router (it soft-navigates without updating
  // useSearchParams), so the tab click would otherwise appear to do nothing.
  const [game, setGameState] = useState(urlGame);

  const setGame = useCallback((next: string) => {
    setGameState(next);
    const url = new URL(window.location.href);
    url.searchParams.set("game", next);
    window.history.replaceState(null, "", url.pathname + url.search);
  }, []);

  // Adopt the URL's game on external navigation (back/forward, fresh load, an
  // in-app link carrying ?game=…), and canonicalize the URL so the active game
  // is always explicit — bare /gift-codes (or an unknown value) becomes
  // ?game=wos. Our own writes use replaceState, which doesn't re-fire
  // useSearchParams, so this won't fight the local selection.
  useEffect(() => {
    setGameState(urlGame);
    if (params.get("game") !== urlGame) {
      const url = new URL(window.location.href);
      url.searchParams.set("game", urlGame);
      window.history.replaceState(null, "", url.pathname + url.search);
    }
  }, [params, urlGame]);

  const [data, setData] = useState<Awaited<ReturnType<typeof fetchGiftCodes>> | null>(
    null,
  );
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { showSuccess, showInfo } = useFeedback();
  const [view, setView] = useState<"active" | "expired">("active");
  const [discordConfig, setDiscordConfig] = useState<GiftCodeDiscordConfig | null>(
    null,
  );
  const [discordToken, setDiscordToken] = useState("");
  const [discordBusy, setDiscordBusy] = useState(false);
  const [discordError, setDiscordError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchGiftCodes(filter, game));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [filter, game]);

  useEffect(() => {
    load();
  }, [load]);

  const loadDiscordConfig = useCallback(async () => {
    try {
      const next = await fetchGiftCodeDiscordConfig();
      setDiscordConfig(next);
      setDiscordToken("");
      setDiscordError(null);
    } catch (e) {
      setDiscordError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    loadDiscordConfig();
  }, [loadDiscordConfig]);

  const saveDiscordConfig = async () => {
    setDiscordBusy(true);
    setDiscordError(null);
    try {
      const next = await updateGiftCodeDiscordConfig({
        bot_token: discordToken || null,
      });
      setDiscordConfig(next);
      setDiscordToken("");
      showSuccess("Discord settings saved.");
    } catch (e) {
      setDiscordError(e instanceof Error ? e.message : String(e));
    } finally {
      setDiscordBusy(false);
    }
  };

  const clearDiscordToken = async () => {
    setDiscordBusy(true);
    setDiscordError(null);
    try {
      const next = await updateGiftCodeDiscordConfig({ clear_token: true });
      setDiscordConfig(next);
      setDiscordToken("");
      showSuccess("Discord token cleared.");
    } catch (e) {
      setDiscordError(e instanceof Error ? e.message : String(e));
    } finally {
      setDiscordBusy(false);
    }
  };

  const runAction = async (action: "scrape" | "redeem") => {
    if (
      action === "scrape" &&
      BETA_GIFT_CODE_GAME_IDS.has(game) &&
      !discordConfig?.token_configured
    ) {
      setError("A Discord token is required to scrape beta gift codes.");
      return;
    }
    setBusy(true);
    try {
      if (action === "scrape") {
        const res = await scrapeGiftCodes(game);
        if (res.count) {
          showSuccess(
            `Found ${res.count} new code(s): ${res.new_codes.join(", ")}`,
          );
        } else {
          showInfo("No new codes.");
        }
      } else {
        const res = await redeemGiftCodes(game);
        if (res.already_running) {
          showInfo("Redeem is already running.");
        } else {
          showSuccess("Redeem finished.");
        }
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const m = data?.metrics;
  const redeemSupported = data?.redeem_supported ?? !BETA_GIFT_CODE_GAME_IDS.has(game);
  // Beta games scrape from Discord, which needs a token. Block the scrape
  // action (and explain why) until one is configured.
  const isBetaGame = BETA_GIFT_CODE_GAME_IDS.has(game);
  const betaTokenMissing = isBetaGame && !discordConfig?.token_configured;

  return (
    <>
      <PageHeader title="Gift codes">
        <p className="muted m-0">
          Century Game promo codes · file{" "}
          <code>{data?.codes_path ?? "db/giftCodes.yaml"}</code>
        </p>
      </PageHeader>

      {KNOWN_GAMES.length > 1 ? (
        <AppTabs
          renderPanels={false}
          selectedKey={game}
          onChange={setGame}
          tabs={KNOWN_GAMES.map((g) => ({
            key: g.id,
            label: g.label,
            title: g.id,
          }))}
        />
      ) : null}

      {BETA_GIFT_CODE_GAME_IDS.has(game) ? (
        <DiscordConfigPanel
          config={discordConfig}
          token={discordToken}
          busy={discordBusy}
          error={discordError}
          onTokenChange={setDiscordToken}
          onSave={saveDiscordConfig}
          onClearToken={clearDiscordToken}
        />
      ) : null}

      <AppTabs
        variant="section"
        renderPanels={false}
        selectedKey={view}
        onChange={(k) => setView(k as "active" | "expired")}
        tabs={[
          {
            key: "active",
            label: `Active codes (${data?.active.length ?? 0})`,
          },
          {
            key: "expired",
            label: `Expired (${data?.expired.length ?? 0})`,
            disabled: (data?.expired.length ?? 0) === 0,
          },
        ]}
      />

      <ErrorBanner message={error} onRetry={load} />

      {data?.parse_error ? (
        <ErrorBanner message={`YAML error: ${data.parse_error}`} />
      ) : null}
      {data?.missing_codes_file ? (
        <p className="muted">Codes file missing — run Scrape.</p>
      ) : null}

      {!redeemSupported ? (
        <section className="panel panel--spaced mb-4">
          <h2 className="m-0">Manual beta apply</h2>
          <p className="muted m-0">
            Beta gift codes are applied inside the beta game client for the
            currently logged-in player.
          </p>
        </section>
      ) : null}

      <NextPollTimer game={game} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-secondary"
          disabled={busy || betaTokenMissing}
          title={
            betaTokenMissing
              ? "A Discord token is required to scrape beta codes"
              : undefined
          }
          onClick={() => runAction("scrape")}
        >
          Scrape now
        </button>
        {redeemSupported ? (
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => runAction("redeem")}
          >
            Redeem now
          </button>
        ) : null}
        <input
          type="search"
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className={INPUT_CLASS}
        />
        <button
          type="button"
          className="btn-secondary"
          onClick={load}
        >
          Reload
        </button>
      </div>

      {m ? (
        <div className="mb-4 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(8rem,1fr))]">
          {[
            {
              label: "Active",
              value: m.active,
              tone: m.active > 0 ? "text-emerald-300" : "text-wos-text",
            },
            {
              label: redeemSupported ? "Needs run" : "Manual apply",
              value: redeemSupported ? m.needs_run : m.active,
              tone:
                (redeemSupported ? m.needs_run : m.active) > 0
                  ? "text-amber-300"
                  : "text-wos-text",
            },
            {
              label: "Pending slots",
              value: m.pending_slots,
              tone: m.pending_slots > 0 ? "text-sky-300" : "text-wos-text",
            },
            { label: "Expired", value: m.expired, tone: "text-wos-text-muted" },
          ].map((item) => (
            <div key={item.label} className="panel !p-3">
              <div className="text-xs uppercase tracking-wide text-wos-text-muted">
                {item.label}
              </div>
              <div className={`mt-1 text-xl font-semibold ${item.tone}`}>
                {item.value}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {data ? (
        <GiftCodesTable
          rows={view === "active" ? data.active : data.expired}
          playerIds={data.player_ids}
          title={
            view === "active"
              ? `Active codes (${data.active.length})`
              : `Expired (${data.expired.length})`
          }
        />
      ) : null}

      {EXTERNAL_ACCOUNT_GAME_IDS.has(game) ? (
        <ExternalAccountsPanel
          games={EXTERNAL_ACCOUNT_GAMES}
          game={game}
          onGameChange={setGame}
        />
      ) : null}
    </>
  );
}

export default function GiftCodesPage() {
  return (
    <Suspense fallback={null}>
      <GiftCodesContent />
    </Suspense>
  );
}
