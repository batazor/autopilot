"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
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
  loadRecent,
  pushRecent,
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
};

function isActivePath(pathname: string, href: string): boolean {
  return (
    pathname === href ||
    (href !== "/overview" && pathname.startsWith(`${href}/`))
  );
}

export function AppNav({ open = false, onNavigate }: AppNavProps) {
  const pathname = usePathname();
  const [recent, setRecent] = useState<RecentNavItem[]>([]);

  const [tier, setTier] = useState<string | null>(null);

  const activeGroup = groupForPath(pathname);

  useEffect(() => {
    setRecent(loadRecent());
  }, []);

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
    <aside
      className={[
        "app-nav fixed inset-y-0 left-0 z-50 flex w-[min(100vw,18rem)] flex-col border-r border-wos-border-subtle bg-wos-surface/98 shadow-2xl shadow-black/25 backdrop-blur-xl transition-transform duration-200 ease-out",
        "lg:static lg:z-auto lg:w-[17.5rem] lg:translate-x-0 lg:shadow-none",
        open ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
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
          className="nav-icon-btn lg:hidden"
          aria-label="Close menu"
          onClick={onNavigate}
        >
          <Icon name="close" size="md" />
        </button>
      </div>

      <div className="nav-body">
        <BotStartBanner />
        <OnboardingChecklist />

        <nav className="nav-scroll px-2 pb-4">
          <div className="nav-pinned">
            <div className="nav-block-label">Quick access</div>
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
          </div>

          {recentVisible ? (
            <div className="nav-recent">
              <div className="nav-block-label">Recent</div>
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
                        <span className="min-w-0 flex-1 truncate">{r.label}</span>
                      </Link>
                    </li>
                  ))}
              </ul>
            </div>
          ) : null}

          <div className="nav-block-label mt-2">Sections</div>

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
