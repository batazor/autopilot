"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { InstanceSelect } from "@/components/InstanceSelect";
import { PlayerSelect } from "@/components/PlayerSelect";
import { useFleet } from "@/components/FleetContextProvider";
import { AppPopover } from "@/components/headless";
import { Icon, type IconName } from "@/components/ui/Icon";
import {
  approvalsHref,
  labelingHref,
  overlayTestHref,
  playerStateHref,
  queueHref,
} from "@/lib/fleet-links";

export type FleetHeaderBarProps = {
  /** Show player dropdown (Player state and similar). */
  showPlayer?: boolean;
  /** Hide quick links when the bar would be redundant (e.g. Instance page). */
  hideQuickLinks?: boolean;
};

type QuickLink = {
  key: string;
  label: string;
  href: string;
  icon: IconName;
  match: (pathname: string) => boolean;
};

/**
 * The instance/player selectors plus the quick-navigation popover, rendered on
 * the right side of {@link PageHeader} when `fleet` is enabled.
 */
export function FleetHeaderBar({
  showPlayer = false,
  hideQuickLinks = false,
}: FleetHeaderBarProps) {
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
  const pathname = usePathname() ?? "";

  const quickLinks: QuickLink[] = [];
  if (instanceId) {
    quickLinks.push({
      key: "labeling",
      label: "Labeling",
      href: labelingHref({ instanceId }),
      icon: "labeling",
      match: (p) => p.startsWith("/labeling"),
    });
    if (playerId) {
      quickLinks.push({
        key: "player",
        label: "Player",
        href: playerStateHref(playerId, { instanceId }),
        icon: "player-state",
        match: (p) => p.startsWith("/player-state"),
      });
    }
    quickLinks.push(
      {
        key: "approvals",
        label: "Approvals",
        href: approvalsHref(instanceId),
        icon: "approvals",
        match: (p) => p.startsWith("/approvals"),
      },
      {
        key: "overlay",
        label: "Overlay",
        href: overlayTestHref(instanceId),
        icon: "overlay-test",
        match: (p) => p.startsWith("/overlay-test"),
      },
      {
        key: "queue",
        label: "Queue",
        href: queueHref({ instanceId }),
        icon: "queue",
        match: (p) => p.startsWith("/queue"),
      },
    );
  }

  return (
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
      {!hideQuickLinks && quickLinks.length > 0 ? (
        <AppPopover
          ariaLabel="Quick navigation"
          buttonTitle="Quick navigation"
          anchor="bottom end"
          trigger={
            <>
              <Icon name="menu" size="sm" />
              <span>Quick menu</span>
            </>
          }
        >
          {({ close }) => (
            <nav aria-label="Fleet shortcuts" className="flex flex-col">
              {quickLinks.map((link) => {
                const active = link.match(pathname);
                return (
                  <Link
                    key={link.key}
                    href={link.href}
                    onClick={() => close()}
                    className={`headless-popover__item${active ? " headless-popover__item--active" : ""}`}
                    aria-current={active ? "page" : undefined}
                  >
                    <span className="headless-popover__item-icon" aria-hidden>
                      <Icon name={link.icon} size="sm" />
                    </span>
                    <span className="flex-1 truncate">{link.label}</span>
                  </Link>
                );
              })}
            </nav>
          )}
        </AppPopover>
      ) : null}
    </div>
  );
}
