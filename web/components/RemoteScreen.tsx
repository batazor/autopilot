"use client";

import { useCallback, useEffect, useState } from "react";
import { ScreenStream } from "@/components/ScreenStream";

/**
 * The helper's view: one device's live screen, tap-through, nothing else.
 *
 * Deliberately outside the dashboard layout — whoever opens this link should
 * see a screen they can click, not a fleet console. The token in the URL is the
 * only credential and the only thing identifying the device; the instance id
 * never reaches the browser.
 */
export function RemoteScreen({ token }: { token: string }) {
  const [state, setState] = useState<"checking" | "ok" | "expired">("checking");
  const [botOnline, setBotOnline] = useState(true);

  useEffect(() => {
    let alive = true;
    fetch(`/api/remote/${encodeURIComponent(token)}`)
      .then((r) => {
        if (!alive) return;
        setState(r.ok ? "ok" : "expired");
      })
      .catch(() => alive && setState("expired"));
    return () => {
      alive = false;
    };
  }, [token]);

  const onTap = useCallback(
    (x: number, y: number) => {
      fetch(`/api/remote/${encodeURIComponent(token)}/tap`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ x, y }),
      })
        .then((r) => (r.ok ? r.json() : { ok: false }))
        .then((data: { ok?: boolean }) => setBotOnline(Boolean(data?.ok)))
        .catch(() => setBotOnline(false));
    },
    [token],
  );

  if (state === "checking") {
    return <main className="remote-page">Подключаемся…</main>;
  }

  if (state === "expired") {
    return (
      <main className="remote-page">
        <div className="remote-notice">
          Ссылка истекла или неверная. Попроси новую.
        </div>
      </main>
    );
  }

  return (
    <main className="remote-page">
      <div className="remote-frame">
        <ScreenStream streamUrl={`/api/remote/${token}/stream`} onTap={onTap} />
      </div>
      <p className="remote-hint">
        {botOnline
          ? "Нашёл предмет — просто кликни по нему. Бот продолжает играть сам."
          : "Бот сейчас не в сети — клики никуда не идут."}
      </p>
    </main>
  );
}
