"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { ApiStatusIndicator } from "@/components/ApiStatusIndicator";
import { BotStartBanner } from "@/components/BotStartBanner";
import { OnboardingChecklist } from "@/components/onboarding/OnboardingChecklist";
import { ThemeToggle } from "@/components/ThemeToggle";
import { EmptyState } from "@/components/ui/EmptyState";
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
  allNavTabs,
  groupForPath,
  labelForHref,
  type NavTab,
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

function highlightMatch(text: string, query: string): ReactNode {
  if (!query) return text;
  const i = text.toLowerCase().indexOf(query);
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <mark className="nav-match">{text.slice(i, i + query.length)}</mark>
      {text.slice(i + query.length)}
    </>
  );
}

export function AppNav({ open = false, onNavigate }: AppNavProps) {
  const pathname = usePathname();
  const router = useRouter();
  const searchRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [recent, setRecent] = useState<RecentNavItem[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);

  const [tier, setTier] = useState<string | null>(null);

  const q = query.trim().toLowerCase();
  const filtering = q.length > 0;
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t) {
        const tag = t.tagName;
        if (
          tag === "INPUT" ||
          tag === "TEXTAREA" ||
          tag === "SELECT" ||
          t.isContentEditable
        ) {
          return;
        }
      }
      e.preventDefault();
      searchRef.current?.focus();
      searchRef.current?.select();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const searchHits = useMemo((): NavTab[] => {
    if (!q) return [];
    return allNavTabs().filter((tab) => {
      const group = NAV_GROUPS.find((g) =>
        g.tabs.some((t) => t.href === tab.href),
      );
      const hay = `${tab.label} ${tab.href} ${tab.description ?? ""} ${group?.label ?? ""}`.toLowerCase();
      return hay.includes(q);
    });
  }, [q]);

  useEffect(() => {
    setActiveIndex(0);
  }, [q]);

  const goToTab = (tab: NavTab) => {
    const lock = getNavLock(tab.href, tier);
    if (lock?.kind === "soon") return;
    const href = lock?.kind === "pro" ? "/license" : tab.href;
    setQuery("");
    onNavigate?.();
    router.push(href);
  };

  const onSearchKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (!filtering || searchHits.length === 0) {
      if (e.key === "Escape") setQuery("");
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % searchHits.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + searchHits.length) % searchHits.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const tab = searchHits[Math.min(activeIndex, searchHits.length - 1)];
      if (tab) goToTab(tab);
    } else if (e.key === "Escape") {
      setQuery("");
    }
  };

  const recentVisible =
    !filtering && recent.filter((r) => r.href !== pathname).length > 0;

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

      <BotStartBanner />
      <OnboardingChecklist />

      <div className="nav-search-wrap">
        <label className="sr-only" htmlFor="nav-filter">
          Filter pages
        </label>
        <span className="nav-search-icon" aria-hidden>
          <Icon name="search" size="sm" />
        </span>
        <input
          ref={searchRef}
          id="nav-filter"
          type="search"
          placeholder="Filter pages…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onSearchKeyDown}
          role="combobox"
          aria-expanded={filtering && searchHits.length > 0}
          aria-controls="nav-search-results"
          aria-activedescendant={
            filtering && searchHits.length > 0
              ? `nav-hit-${Math.min(activeIndex, searchHits.length - 1)}`
              : undefined
          }
          className="nav-search-input"
        />
        {!filtering ? (
          <kbd className="nav-search-kbd" aria-hidden>
            /
          </kbd>
        ) : (
          <button
            type="button"
            className="nav-search-clear"
            onClick={() => setQuery("")}
            aria-label="Clear filter"
          >
            <Icon name="clear" size="sm" />
          </button>
        )}
      </div>

      <nav className="nav-scroll flex-1 overflow-y-auto px-2 pb-4">
        {!filtering ? (
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
                  query={q}
                  variant="pinned"
                  lock={getNavLock(item.href, tier) ?? undefined}
                  onNavigate={onNavigate}
                />
              ))}
            </ul>
          </div>
        ) : null}

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

        {!filtering ? (
          <div className="nav-block-label mt-2">Sections</div>
        ) : (
          <div className="nav-block-label mt-2">
            {searchHits.length > 0 ? "Results" : "No match"}
          </div>
        )}

        {filtering ? (
          searchHits.length === 0 ? (
            <EmptyState
              className="mx-2 border-0 bg-transparent py-6"
              icon="search"
              title="No pages match"
              description="Try another filter or clear the search."
            />
          ) : (
            <ul className="nav-list" id="nav-search-results" role="listbox">
              {searchHits.map((tab, i) => (
                <NavRow
                  key={tab.href}
                  id={`nav-hit-${i}`}
                  href={tab.href}
                  label={tab.label}
                  description={tab.description}
                  active={isActivePath(pathname, tab.href)}
                  highlighted={i === Math.min(activeIndex, searchHits.length - 1)}
                  query={q}
                  lock={getNavLock(tab.href, tier) ?? undefined}
                  onNavigate={onNavigate}
                />
              ))}
            </ul>
          )
        ) : (
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
                      <span className="nav-link__label">
                        {highlightMatch(group.label, q)}
                      </span>
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
                              href={lock?.kind === "pro" ? "/license" : tab.href}
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
        )}
      </nav>

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
  id,
  href,
  label,
  description,
  active,
  highlighted = false,
  query,
  variant = "default",
  lock,
  onNavigate,
}: {
  id?: string;
  href: string;
  label: string;
  description?: string;
  active: boolean;
  highlighted?: boolean;
  query: string;
  variant?: "default" | "pinned";
  lock?: NavLock;
  onNavigate?: () => void;
}) {
  const disabling = isLockDisabling(lock);
  const showDesc = active && description;
  const linkHref = lock?.kind === "pro" ? "/license" : href;
  const title = lock ? lock.tooltip : description;

  const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (lock?.kind === "soon") {
      e.preventDefault();
      return;
    }
    onNavigate?.();
  };

  return (
    <li role={id ? "option" : undefined} aria-selected={id ? highlighted : undefined}>
      <Link
        id={id}
        href={linkHref}
        onClick={handleClick}
        className={[
          "nav-link",
          active && !disabling ? "nav-link--active" : "",
          highlighted ? "nav-link--highlighted" : "",
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
            <span>{highlightMatch(label, query)}</span>
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
