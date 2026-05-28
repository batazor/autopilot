"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiStatusIndicator } from "@/components/ApiStatusIndicator";
import { BotStartBanner } from "@/components/BotStartBanner";
import { OnboardingChecklist } from "@/components/onboarding/OnboardingChecklist";
import { ThemeToggle } from "@/components/ThemeToggle";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { NavIcon } from "@/components/ui/NavIcon";
import { fetchLicenseStatus } from "@/lib/api";
import {
  getNavLock,
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
  const searchRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [recent, setRecent] = useState<RecentNavItem[]>([]);

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
          <span className="nav-brand__logo">A</span>
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
            <ul className="nav-list">
              {searchHits.map((tab) => (
                <NavRow
                  key={tab.href}
                  href={tab.href}
                  label={tab.label}
                  description={tab.description}
                  active={isActivePath(pathname, tab.href)}
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
                      {groupActive ? (
                        <span className="nav-link__desc">
                          {group.tabs.length} pages · use tabs above
                        </span>
                      ) : (
                        <span className="nav-link__desc truncate">
                          {group.description}
                        </span>
                      )}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </nav>

      <footer className="nav-footer">
        <ThemeToggle compact className="mb-2 w-full justify-start" />
        <p className="m-0 flex flex-wrap items-center gap-x-1.5 gap-y-1">
          <span>
            API <code className="nav-footer__code">:8765</code>
          </span>
          <span className="text-wos-text-muted" aria-hidden>
            ·
          </span>
          <ApiStatusIndicator variant="footer" />
          <span className="text-wos-text-muted" aria-hidden>
            ·
          </span>
          <a
            href="/health"
            target="_blank"
            rel="noreferrer"
            className="no-underline"
            style={{ color: "var(--wos-link)" }}
          >
            health JSON
          </a>
          <span className="text-wos-text-muted" aria-hidden>
            ·
          </span>
          <a
            href="https://discord.gg/62twnzKG9"
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1 no-underline"
            style={{ color: "var(--wos-link)" }}
            title="Join the Autopilot community on Discord"
          >
            <Icon name="discord" size="sm" />
            Discord
          </a>
        </p>
      </footer>
    </aside>
  );
}


function NavRow({
  href,
  label,
  description,
  active,
  query,
  variant = "default",
  lock,
  onNavigate,
}: {
  href: string;
  label: string;
  description?: string;
  active: boolean;
  query: string;
  variant?: "default" | "pinned";
  lock?: NavLock;
  onNavigate?: () => void;
}) {
  const locked = Boolean(lock);
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
    <li>
      <Link
        href={linkHref}
        onClick={handleClick}
        className={[
          "nav-link",
          active && !locked ? "nav-link--active" : "",
          variant === "pinned" ? "nav-link--pinned" : "",
          locked ? "opacity-60" : "",
          lock?.kind === "soon" ? "cursor-not-allowed" : "",
        ].join(" ")}
        aria-current={active && !locked ? "page" : undefined}
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
