"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useFleet } from "@/components/FleetContextProvider";
import { AppListbox } from "@/components/headless";
import { ScreenStream } from "@/components/ScreenStream";
import {
  createScreenShare,
  fetchScreenShare,
  postScreenTap,
  revokeScreenShare,
} from "@/lib/api";
import { Button } from "./Button";

function ttlLabel(seconds: number): string {
  if (seconds <= 0) return "истекла";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h > 0 ? `ещё ${h} ч ${m} мин` : `ещё ${m} мин`;
}

/**
 * Hand the live screen to other people so they can click items the bot's scene
 * DB doesn't know yet. The bot keeps playing throughout — a helper's tap is
 * just another touch on the device, not a request the solver waits for.
 *
 * The share link is the entire access model (see ``api.services.remote_control``):
 * unguessable, expiring, one device. The operator can also click right here,
 * which exercises exactly the same path a helper's click takes.
 */
export function RemoteHelpTab() {
  const { instanceId, instances, setInstanceId, instancesLoading } = useFleet();
  const [token, setToken] = useState<string | null>(null);
  const [ttl, setTtl] = useState(0);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [tapNote, setTapNote] = useState<string | null>(null);

  const instanceOptions = useMemo(
    () => instances.map((id) => ({ value: id, label: id })),
    [instances],
  );

  useEffect(() => {
    setToken(null);
    setTtl(0);
    setCopied(false);
    if (!instanceId) return;
    let alive = true;
    fetchScreenShare(instanceId)
      .then((share) => {
        if (!alive) return;
        setToken(share.token);
        setTtl(share.ttl_s);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [instanceId]);

  const shareUrl =
    token && typeof window !== "undefined"
      ? `${window.location.origin}/remote/${token}`
      : null;

  const mint = async () => {
    if (!instanceId) return;
    setBusy(true);
    try {
      const share = await createScreenShare(instanceId);
      setToken(share.token);
      setTtl(share.ttl_s);
      setCopied(false);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async () => {
    if (!instanceId) return;
    setBusy(true);
    try {
      await revokeScreenShare(instanceId);
      setToken(null);
      setTtl(0);
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const onTap = useCallback(
    (x: number, y: number) => {
      if (!instanceId) return;
      postScreenTap(instanceId, x, y)
        .then((res) =>
          setTapNote(res.ok ? null : "Бот не в сети — клик не доставлен"),
        )
        .catch(() => setTapNote("Клик не доставлен"));
    },
    [instanceId],
  );

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <AppListbox
          label="Instance"
          options={instanceOptions}
          value={instanceId}
          onChange={setInstanceId}
          loading={instancesLoading}
          placeholder="Select a device"
          inline
        />
        <Button variant="primary" onClick={mint} disabled={!instanceId || busy}>
          {token ? "Продлить ссылку" : "Создать ссылку"}
        </Button>
        {token ? (
          <>
            <Button onClick={copy} disabled={!shareUrl}>
              {copied ? "Скопировано" : "Копировать"}
            </Button>
            <Button
              onClick={() => shareUrl && window.open(shareUrl, "_blank")}
              disabled={!shareUrl}
              title="Тот же экран во весь размер, без дашборда"
            >
              Открыть вкладкой
            </Button>
            <Button onClick={revoke} disabled={busy}>
              Отозвать
            </Button>
          </>
        ) : null}
      </div>

      {shareUrl ? (
        <div className="flex flex-col gap-1">
          <code className="break-all rounded-md border border-wos-border bg-wos-panel-raised px-3 py-2 text-xs">
            {shareUrl}
          </code>
          <span className="text-xs text-wos-text-muted">
            Действует {ttlLabel(ttl)}. Открывший видит только этот экран и может
            по нему кликать.
          </span>
        </div>
      ) : (
        <span className="text-xs text-wos-text-muted">
          Ссылки нет. Создай — и отправь тем, кто будет помогать искать предметы.
        </span>
      )}

      {instanceId ? (
        <div className="max-w-sm">
          <ScreenStream instanceId={instanceId} onTap={onTap} />
          <p className="mt-2 text-xs text-wos-text-muted">
            {tapNote ??
              "Кликай прямо здесь — тап уходит на устройство, бот продолжает играть."}
          </p>
        </div>
      ) : null}
    </section>
  );
}
