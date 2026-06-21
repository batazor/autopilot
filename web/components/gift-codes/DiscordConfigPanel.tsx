import { ErrorBanner } from "@/components/feedback";
import { Button } from "@/components/ui";
import type { GiftCodeDiscordConfig } from "@/lib/api";
import { INPUT_CLASS, LABEL_CLASS } from "@/lib/gift-codes/types";

export function DiscordConfigPanel({
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
              className={`status-pill ${config?.token_configured ? "pill-live" : "pill-paused"}`}
            >
              {config?.token_configured ? "Token configured" : "Token missing"}
            </span>
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy || !config?.token_configured} onClick={onClearToken}>
            Clear token
          </Button>
          <Button variant="primary" disabled={busy} onClick={onSave}>
            Save Discord
          </Button>
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
