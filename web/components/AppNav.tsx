"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ApiStatusIndicator } from "@/components/ApiStatusIndicator";
import { BotStartBanner } from "@/components/BotStartBanner";
import { OnboardingChecklist } from "@/components/onboarding/OnboardingChecklist";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Icon } from "@/components/ui/Icon";
import { NavIcon } from "@/components/ui/NavIcon";
import { VersionFooterRow } from "@/components/VersionBadge";
import { fetchLicenseStatus } from "@/lib/api";
import {
  getNavLock,
  isLockDisabling,
  NAV_LOCK_BADGE,
  type NavLock,
} from "@/lib/nav-locks";
import {
  clearDockPos,
  loadDockPos,
  loadQuickAccessCollapsed,
  loadRecent,
  loadSectionCollapsed,
  pushRecent,
  saveDockPos,
  saveQuickAccessCollapsed,
  saveSectionCollapsed,
  type DockPos,
  type RecentNavItem,
} from "@/lib/nav-prefs";
import {
  NAV_GROUPS,
  NAV_PINNED,
  NAV_PINNED_HREFS,
  groupForPath,
  labelForHref,
} from "@/lib/nav";

type AppNavProps = {
  open?: boolean;
  onNavigate?: () => void;
  /** Desktop-only: when true the sidebar is hidden (md+). */
  collapsed?: boolean;
  /** Desktop-only: collapse the whole sidebar. */
  onCollapse?: () => void;
  /** Open the command palette (also bound to Cmd/Ctrl+K globally). */
  onOpenPalette?: () => void;
};

function isActivePath(pathname: string, href: string): boolean {
  return (
    pathname === href ||
    (href !== "/overview" && pathname.startsWith(`${href}/`))
  );
}

export function AppNav({
  open = false,
  onNavigate,
  collapsed = false,
  onCollapse,
  onOpenPalette,
}: AppNavProps) {
  const pathname = usePathname();
  const [recent, setRecent] = useState<RecentNavItem[]>([]);
  const [quickCollapsed, setQuickCollapsed] = useState(false);
  // Per-block collapse (keys: "recent", "sections"), persisted as a record.
  const [blockCollapsed, setBlockCollapsed] = useState<Record<string, boolean>>(
    {},
  );
  // Resolved after mount to avoid an SSR/client hydration mismatch.
  const [shortcut, setShortcut] = useState("");

  const [tier, setTier] = useState<string | null>(null);

  const activeGroup = groupForPath(pathname);

  useEffect(() => {
    setRecent(loadRecent());
    setQuickCollapsed(loadQuickAccessCollapsed());
    setBlockCollapsed(loadSectionCollapsed());
    const isMac = /Mac|iPhone|iPad/.test(
      navigator.platform || navigator.userAgent,
    );
    setShortcut(isMac ? "⌘K" : "Ctrl K");
  }, []);

  const toggleQuickAccess = () => {
    setQuickCollapsed((prev) => {
      const next = !prev;
      saveQuickAccessCollapsed(next);
      return next;
    });
  };

  const toggleBlock = (key: string) => {
    setBlockCollapsed((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      saveSectionCollapsed(next);
      return next;
    });
  };

  useEffect(() => {
    let cancelled = false;
    const pull = () => {
      fetchLicenseStatus()
        .then((st) => {
          if (cancelled) return;
          setTier(st.active && st.tier ? st.tier : null);
        })
        .catch(() => {
          if (!cancelled) setTier(null);
        });
    };
    pull();
    const id = window.setInterval(pull, 30_000);
    window.addEventListener("wos:license:updated", pull);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      window.removeEventListener("wos:license:updated", pull);
    };
  }, []);

  useEffect(() => {
    if (!pathname) return;
    const label = labelForHref(pathname);
    pushRecent(pathname, label);
    setRecent(loadRecent());
  }, [pathname]);

  const recentVisible = recent.filter((r) => r.href !== pathname).length > 0;

  return (
    <>
    <aside
      className={[
        "app-nav fixed inset-y-0 left-0 z-50 flex w-[min(100vw,18rem)] flex-col border-r border-wos-border-subtle bg-wos-surface/98 shadow-2xl shadow-black/25 backdrop-blur-xl transition-transform duration-200 ease-out",
        "md:sticky md:top-0 md:z-auto md:w-[17.5rem] md:shrink-0 md:translate-x-0 md:shadow-none",
        open ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        collapsed ? "md:hidden" : "",
      ].join(" ")}
      aria-label="Main navigation"
    >
      <div className="nav-brand">
        <Link
          href="/overview"
          onClick={onNavigate}
          className="group flex min-w-0 flex-1 items-center gap-2.5 no-underline"
        >
          <span className="nav-brand__logo">
            <Image
              src="/logo.png"
              alt=""
              width={36}
              height={36}
              priority
              className="h-full w-full object-contain"
            />
          </span>
          <span className="min-w-0">
            <span className="flex items-center gap-1.5">
              <span className="truncate text-sm font-semibold tracking-tight text-wos-text group-hover:text-white">
                Autopilot
              </span>
              {tier ? (
                <span
                  className="rounded-full border border-wos-border-subtle bg-wos-panel-raised px-1.5 py-0 text-[10px] font-semibold uppercase tracking-wide text-wos-text-secondary"
                  title={`License tier: ${tier}`}
                >
                  {tier}
                </span>
              ) : null}
            </span>
            <span className="block text-[11px] text-wos-text-muted">
              Operations dashboard
            </span>
          </span>
        </Link>
        <button
          type="button"
          className="nav-icon-btn md:hidden"
          aria-label="Close menu"
          onClick={onNavigate}
        >
          <Icon name="close" size="md" />
        </button>
        {onCollapse ? (
          <button
            type="button"
            className="nav-icon-btn hidden md:inline-flex"
            aria-label="Collapse menu"
            title="Collapse menu"
            onClick={onCollapse}
          >
            <Icon name="chevron-left" size="md" />
          </button>
        ) : null}
      </div>

      <div className="nav-body">
        <BotStartBanner />
        <OnboardingChecklist />

        <nav className="nav-scroll px-2 pb-4">
          {onOpenPalette ? (
            <button
              type="button"
              className="nav-search"
              onClick={onOpenPalette}
              title="Search pages & actions"
            >
              <span className="nav-search__icon" aria-hidden>
                <Icon name="search" size="sm" />
              </span>
              <span className="nav-search__label">Search…</span>
              {shortcut ? <kbd className="nav-search__kbd">{shortcut}</kbd> : null}
            </button>
          ) : null}

          <div className="nav-pinned">
            <button
              type="button"
              className={[
                "nav-block-label nav-block-toggle",
                // No list below when collapsed → drop the gap so the label
                // sits vertically centered in the pinned box.
                quickCollapsed ? "mb-0" : "",
              ].join(" ")}
              onClick={toggleQuickAccess}
              aria-expanded={!quickCollapsed}
              title={quickCollapsed ? "Expand Quick access" : "Collapse Quick access"}
            >
              <span>Quick access</span>
              <Icon
                name={quickCollapsed ? "arrow-down" : "arrow-up"}
                size="sm"
              />
            </button>
            {!quickCollapsed ? (
              <ul className="nav-list">
                {NAV_PINNED.map((item) => (
                  <NavRow
                    key={item.href}
                    href={item.href}
                    label={item.label}
                    description={item.description}
                    active={isActivePath(pathname, item.href)}
                    variant="pinned"
                    lock={getNavLock(item.href, tier) ?? undefined}
                    onNavigate={onNavigate}
                  />
                ))}
              </ul>
            ) : null}
          </div>

          {recentVisible ? (
            <div className="nav-recent">
              <button
                type="button"
                className="nav-block-label nav-block-toggle"
                onClick={() => toggleBlock("recent")}
                aria-expanded={!blockCollapsed.recent}
                title={blockCollapsed.recent ? "Expand Recent" : "Collapse Recent"}
              >
                <span>Recent</span>
                <Icon
                  name={blockCollapsed.recent ? "arrow-down" : "arrow-up"}
                  size="sm"
                />
              </button>
              {!blockCollapsed.recent ? (
                <ul className="nav-list">
                  {recent
                    .filter(
                      (r) =>
                        r.href !== pathname && !NAV_PINNED_HREFS.has(r.href),
                    )
                    .slice(0, 4)
                    .map((r) => (
                      <li key={r.href}>
                        <Link
                          href={r.href}
                          onClick={onNavigate}
                          className="nav-link nav-link--compact"
                        >
                          <span className="nav-link__icon" aria-hidden>
                            <Icon name="recent" size="sm" />
                          </span>
                          <span className="min-w-0 flex-1 truncate">
                            {r.label}
                          </span>
                        </Link>
                      </li>
                    ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          <button
            type="button"
            className="nav-block-label nav-block-toggle mt-2"
            onClick={() => toggleBlock("sections")}
            aria-expanded={!blockCollapsed.sections}
            title={blockCollapsed.sections ? "Expand Sections" : "Collapse Sections"}
          >
            <span>Sections</span>
            <Icon
              name={blockCollapsed.sections ? "arrow-down" : "arrow-up"}
              size="sm"
            />
          </button>

          {blockCollapsed.sections ? null : (
          <ul className="nav-list">
            {NAV_GROUPS.map((group) => {
              const groupActive = activeGroup?.id === group.id;
              return (
                <li key={group.id}>
                  <Link
                    href={group.defaultHref}
                    onClick={onNavigate}
                    className={[
                      "nav-link",
                      groupActive ? "nav-link--active" : "",
                    ].join(" ")}
                    aria-current={groupActive ? "true" : undefined}
                    title={group.description}
                  >
                    <span className="nav-link__icon" aria-hidden>
                      <NavIcon groupId={group.id} size="sm" />
                    </span>
                    <span className="nav-link__body">
                      <span className="nav-link__label">{group.label}</span>
                      <span className="nav-link__desc truncate">
                        {group.description}
                      </span>
                    </span>
                  </Link>
                  {groupActive ? (
                    <ul className="nav-sublist">
                      {group.tabs.map((tab) => {
                        const tabActive = isActivePath(pathname, tab.href);
                        const lock = getNavLock(tab.href, tier) ?? undefined;
                        const disabling = isLockDisabling(lock);
                        return (
                          <li key={tab.href}>
                            <Link
                              href={lock?.kind === "pro" || lock?.kind === "r4" ? "/license" : tab.href}
                              onClick={(e) => {
                                if (lock?.kind === "soon") {
                                  e.preventDefault();
                                  return;
                                }
                                onNavigate?.();
                              }}
                              className={[
                                "nav-link nav-link--compact nav-sublist__link",
                                tabActive && !disabling
                                  ? "nav-sublist__link--active"
                                  : "",
                                disabling ? "opacity-60" : "",
                                lock?.kind === "soon" ? "cursor-not-allowed" : "",
                              ].join(" ")}
                              aria-current={
                                tabActive && !disabling ? "page" : undefined
                              }
                              aria-disabled={
                                lock?.kind === "soon" ? true : undefined
                              }
                              title={lock ? lock.tooltip : tab.description}
                            >
                              <span className="nav-link__icon" aria-hidden>
                                <NavIcon href={tab.href} size="sm" />
                              </span>
                              <span className="min-w-0 flex-1 truncate">
                                {tab.label}
                              </span>
                              {lock ? (
                                <span
                                  className="rounded-full border border-amber-400/40 bg-amber-500/15 px-1.5 py-0 text-[9px] font-semibold uppercase tracking-wide text-amber-300"
                                  aria-label={lock.tooltip}
                                >
                                  {NAV_LOCK_BADGE[lock.kind]}
                                </span>
                              ) : null}
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  ) : null}
                </li>
              );
            })}
          </ul>
          )}
        </nav>
      </div>

      <footer className="nav-footer">
        <ThemeToggle compact className="nav-footer__theme" />

        <VersionFooterRow />

        <a
          href="https://discord.gg/62twnzKG9"
          target="_blank"
          rel="noreferrer noopener"
          className="nav-footer__discord"
          title="Join the Autopilot community on Discord"
        >
          <span className="nav-footer__discord-icon" aria-hidden>
            <Icon name="discord" size="sm" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[12px] font-semibold leading-tight">
              Join Discord
            </span>
            <span className="block truncate text-[10px] text-wos-text-muted">
              Community & support
            </span>
          </span>
        </a>

        <div className="nav-footer__status">
          <span className="inline-flex items-center gap-1">
            <span>API</span>
            <code className="nav-footer__code">:8765</code>
          </span>
          <span className="nav-footer__sep" aria-hidden>·</span>
          <ApiStatusIndicator variant="footer" />
          <span className="nav-footer__sep" aria-hidden>·</span>
          <a
            href="/health"
            target="_blank"
            rel="noreferrer"
            className="nav-footer__link"
          >
            health JSON
          </a>
        </div>
      </footer>
    </aside>

    {/* When the sidebar is collapsed (desktop), Bot control still needs to be
        reachable, so it floats as a draggable standalone block over the page.
        Hidden on mobile, where the drawer covers it. */}
    {collapsed && onCollapse ? <NavDock onExpand={onCollapse} /> : null}
    </>
  );
}


// Floating, draggable Bot-control dock shown while the sidebar is collapsed.
// Drag by the header strip; the position persists in localStorage and is
// clamped to the viewport so it can never be dragged fully off-screen.
function NavDock({ onExpand }: { onExpand: () => void }) {
  const [pos, setPos] = useState<DockPos | null>(null);
  const elRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ dx: number; dy: number } | null>(null);
  const posRef = useRef<DockPos | null>(null);

  useEffect(() => {
    setPos(loadDockPos());
  }, []);

  const MARGIN = 12;
  const SNAP = 28;

  const bounds = () => {
    const el = elRef.current;
    const w = el?.offsetWidth ?? 288;
    const h = el?.offsetHeight ?? 200;
    return {
      maxX: Math.max(MARGIN, window.innerWidth - w - MARGIN),
      maxY: Math.max(MARGIN, window.innerHeight - h - MARGIN),
    };
  };

  const clamp = (x: number, y: number): DockPos => {
    const { maxX, maxY } = bounds();
    return {
      x: Math.min(Math.max(MARGIN, x), maxX),
      y: Math.min(Math.max(MARGIN, y), maxY),
    };
  };

  // Magnetic edges: when a drop lands near an edge, tuck it flush to that edge
  // (so corners "click" into place), but free placement is kept elsewhere.
  const snap = ({ x, y }: DockPos): DockPos => {
    const { maxX, maxY } = bounds();
    return {
      x: x - MARGIN <= SNAP ? MARGIN : maxX - x <= SNAP ? maxX : x,
      y: y - MARGIN <= SNAP ? MARGIN : maxY - y <= SNAP ? maxY : y,
    };
  };

  const onPointerDown = (e: React.PointerEvent) => {
    // Let clicks on the expand button through without starting a drag.
    if ((e.target as HTMLElement).closest("button")) return;
    const el = elRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    dragRef.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragRef.current) return;
    const next = clamp(e.clientX - dragRef.current.dx, e.clientY - dragRef.current.dy);
    posRef.current = next;
    setPos(next);
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
    if (posRef.current) {
      const snapped = snap(posRef.current);
      posRef.current = snapped;
      setPos(snapped);
      saveDockPos(snapped);
    }
  };

  const resetPosition = () => {
    clearDockPos();
    posRef.current = null;
    setPos(null);
  };

  const style = pos ? { left: pos.x, top: pos.y, right: "auto" as const } : undefined;

  return (
    <div ref={elRef} className="nav-dock hidden md:block" style={style}>
      <div
        className="nav-dock__head"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        title="Drag to move"
      >
        <span className="nav-dock__grip" aria-hidden>
          ⠿
        </span>
        <span className="flex items-center">
          <button
            type="button"
            className="nav-icon-btn"
            aria-label="Reset position"
            title="Reset position"
            onClick={resetPosition}
          >
            <Icon name="refresh" size="sm" />
          </button>
          <button
            type="button"
            className="nav-icon-btn"
            aria-label="Expand menu"
            title="Expand menu"
            onClick={onExpand}
          >
            <Icon name="chevron-right" size="md" />
          </button>
        </span>
      </div>
      <BotStartBanner />
    </div>
  );
}


function NavRow({
  href,
  label,
  description,
  active,
  variant = "default",
  lock,
  onNavigate,
}: {
  href: string;
  label: string;
  description?: string;
  active: boolean;
  variant?: "default" | "pinned";
  lock?: NavLock;
  onNavigate?: () => void;
}) {
  const disabling = isLockDisabling(lock);
  const showDesc = active && description;
  const linkHref = lock?.kind === "pro" || lock?.kind === "r4" ? "/license" : href;
  const title = lock ? lock.tooltip : description;

  const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (lock?.kind === "soon") {
      e.preventDefault();
      return;
    }
    onNavigate?.();
  };

  return (
    <li>
      <Link
        href={linkHref}
        onClick={handleClick}
        className={[
          "nav-link",
          active && !disabling ? "nav-link--active" : "",
          variant === "pinned" ? "nav-link--pinned" : "",
          disabling ? "opacity-60" : "",
          lock?.kind === "soon" ? "cursor-not-allowed" : "",
        ].join(" ")}
        aria-current={active && !disabling ? "page" : undefined}
        aria-disabled={lock?.kind === "soon" ? true : undefined}
        title={title}
      >
        <span className="nav-link__icon" aria-hidden>
          <NavIcon href={href} size="sm" />
        </span>
        <span className="nav-link__body">
          <span className="nav-link__label flex items-center gap-1.5">
            <span>{label}</span>
            {lock ? (
              <span
                className="rounded-full border border-amber-400/40 bg-amber-500/15 px-1.5 py-0 text-[9px] font-semibold uppercase tracking-wide text-amber-300"
                aria-label={lock.tooltip}
              >
                {NAV_LOCK_BADGE[lock.kind]}
              </span>
            ) : null}
          </span>
          {showDesc ? (
            <span className="nav-link__desc">{description}</span>
          ) : null}
        </span>
      </Link>
    </li>
  );
}
