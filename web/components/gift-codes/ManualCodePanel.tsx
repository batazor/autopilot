"use client";

import { useState } from "react";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { Button } from "@/components/ui";
import { addGiftCode } from "@/lib/api";
import { INPUT_CLASS, LABEL_CLASS } from "@/lib/gift-codes/types";

/** Manual gift-code entry for builds with no automatic source (the RU shard).
 *
 * Codes are typed by the operator, stored under the build's game id, and later
 * applied in-game on the device by the redeem cron. After a successful add we
 * call ``onAdded`` so the parent reloads the code table.
 */
export function ManualCodePanel({
  game,
  onAdded,
}: {
  game: string;
  onAdded: () => void;
}) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { showSuccess, showInfo } = useFeedback();

  const submit = async () => {
    const value = code.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await addGiftCode(game, value);
      if (res.created) {
        showSuccess(`Added code ${res.code}.`);
      } else {
        showInfo(`Code ${res.code} already in the list.`);
      }
      setCode("");
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel panel--spaced mb-4">
      <h2 className="m-0">Add a code manually</h2>
      <p className="muted mt-1">
        This build has no automatic code source — paste codes from the community
        here. They&rsquo;re applied in-game on the device on the next redeem run.
      </p>

      <ErrorBanner message={error} />

      <form
        className="mt-3 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label className="form-field grow">
          <span className={LABEL_CLASS}>Gift code</span>
          <input
            type="text"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            placeholder="e.g. ABC123"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className={INPUT_CLASS}
          />
        </label>
        <Button type="submit" variant="primary" disabled={busy || !code.trim()}>
          Add code
        </Button>
      </form>
    </section>
  );
}
