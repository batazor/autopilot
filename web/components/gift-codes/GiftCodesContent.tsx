"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ExternalAccountsPanel } from "@/components/gift-codes/ExternalAccountsPanel";
import { AppTabs } from "@/components/headless";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { PageHeader } from "@/components/PageHeader";
import { Button, MetricCard, MetricGrid, type MetricTone } from "@/components/ui";
import {
  fetchGiftCodeDiscordConfig,
  fetchGiftCodes,
  redeemGiftCodes,
  scrapeGiftCodes,
  updateGiftCodeDiscordConfig,
  type GiftCodeDiscordConfig,
} from "@/lib/api";
import {
  BETA_GIFT_CODE_GAME_IDS,
  DEFAULT_GAME,
  EXTERNAL_ACCOUNT_GAME_IDS,
  EXTERNAL_ACCOUNT_GAMES,
  INPUT_CLASS,
  KNOWN_GAMES,
} from "@/lib/gift-codes/types";
import { DiscordConfigPanel } from "./DiscordConfigPanel";
import { GiftCodesTable } from "./GiftCodesTable";
import { NextPollTimer } from "./NextPollTimer";

export function GiftCodesContent() {
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

  const [data, setData] = useState<Awaited<ReturnType<typeof fetchGiftCodes>> | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { showSuccess, showInfo } = useFeedback();
  const [view, setView] = useState<"active" | "expired">("active");
  const [discordConfig, setDiscordConfig] = useState<GiftCodeDiscordConfig | null>(null);
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
      const next = await updateGiftCodeDiscordConfig({ bot_token: discordToken || null });
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
          showSuccess(`Found ${res.count} new code(s): ${res.new_codes.join(", ")}`);
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
          tabs={KNOWN_GAMES.map((g) => ({ key: g.id, label: g.label, title: g.id }))}
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
          { key: "active", label: `Active codes (${data?.active.length ?? 0})` },
          {
            key: "expired",
            label: `Expired (${data?.expired.length ?? 0})`,
            disabled: (data?.expired.length ?? 0) === 0,
          },
        ]}
      />

      <ErrorBanner message={error} onRetry={load} />

      {data?.parse_error ? <ErrorBanner message={`YAML error: ${data.parse_error}`} /> : null}
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
        <Button
          disabled={busy || betaTokenMissing}
          title={betaTokenMissing ? "A Discord token is required to scrape beta codes" : undefined}
          onClick={() => runAction("scrape")}
        >
          Scrape now
        </Button>
        {redeemSupported ? (
          <Button variant="primary" disabled={busy} onClick={() => runAction("redeem")}>
            Redeem now
          </Button>
        ) : null}
        <input
          type="search"
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className={INPUT_CLASS}
        />
        <Button onClick={load}>Reload</Button>
      </div>

      {m ? (
        <MetricGrid className="mb-4">
          {(
            [
              {
                label: "Active",
                value: m.active,
                tone: m.active > 0 ? "ok" : "neutral",
              },
              {
                label: redeemSupported ? "Needs run" : "Manual apply",
                value: redeemSupported ? m.needs_run : m.active,
                tone: (redeemSupported ? m.needs_run : m.active) > 0 ? "warn" : "neutral",
              },
              {
                label: "Pending slots",
                value: m.pending_slots,
                tone: m.pending_slots > 0 ? "accent" : "neutral",
              },
              { label: "Expired", value: m.expired, tone: "neutral" },
            ] as { label: string; value: number; tone: MetricTone }[]
          ).map((item) => (
            <MetricCard key={item.label} label={item.label} value={item.value} tone={item.tone} />
          ))}
        </MetricGrid>
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
