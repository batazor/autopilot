"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { InstanceSelect } from "@/components/InstanceSelect";
import { PlayerSelect } from "@/components/PlayerSelect";
import { useFleet } from "@/components/FleetContextProvider";
import {
  approvalsHref,
  labelingHref,
  overlayTestHref,
  playerStateHref,
  queueHref,
} from "@/lib/fleet-links";

type FleetPageHeaderProps = {
  title: string;
  children?: ReactNode;
  /** Show player dropdown (Player state and similar). */
  showPlayer?: boolean;
  /** Hide quick links when the bar would be redundant (e.g. Instance page). */
  hideQuickLinks?: boolean;
};

export function FleetPageHeader({
  title,
  children,
  showPlayer = false,
  hideQuickLinks = false,
}: FleetPageHeaderProps) {
  const {
    instances,
    players,
    instanceId,
    playerId,
    setInstanceId,
    setPlayerId,
    instancesLoading,
    playersLoading,
  } = useFleet();

  return (
    <header className="app-header">
      <div className="min-w-0 flex-1">
        <h1>{title}</h1>
        {children ? (
          <div className="mt-1 max-w-4xl text-sm text-wos-text-secondary">
            {children}
          </div>
        ) : null}
      </div>
      <div className="fleet-header-bar">
        <InstanceSelect
          instances={instances}
          value={instanceId}
          onChange={setInstanceId}
          loading={instancesLoading}
        />
        {showPlayer ? (
          <PlayerSelect
            players={players}
            value={playerId}
            onChange={setPlayerId}
            loading={playersLoading}
          />
        ) : null}
        {!hideQuickLinks && instanceId ? (
          <nav className="fleet-header-links" aria-label="Fleet shortcuts">
            <Link href={labelingHref({ instanceId })} className="fleet-header-link">
              Labeling
            </Link>
            {playerId ? (
              <Link
                href={playerStateHref(playerId, { instanceId })}
                className="fleet-header-link"
              >
                Player
              </Link>
            ) : null}
            <Link href={approvalsHref(instanceId)} className="fleet-header-link">
              Approvals
            </Link>
            <Link href={overlayTestHref(instanceId)} className="fleet-header-link">
              Overlay
            </Link>
            <Link href={queueHref({ instanceId })} className="fleet-header-link">
              Queue
            </Link>
          </nav>
        ) : null}
      </div>
    </header>
  );
}
