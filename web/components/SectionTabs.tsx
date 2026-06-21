"use client";

import { Tab, TabGroup, TabList } from "@headlessui/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_GROUPS, type NavGroupId } from "@/lib/nav-groups";
import { getNavLock, isLockDisabling, NAV_LOCK_BADGE } from "@/lib/nav-locks";

function isActivePath(pathname: string, href: string): boolean {
  return (
    pathname === href ||
    (href !== "/overview" && pathname.startsWith(`${href}/`))
  );
}

export function SectionTabs({ groupId }: { groupId: NavGroupId }) {
  const pathname = usePathname();
  const group = NAV_GROUPS.find((g) => g.id === groupId);

  if (!group) return null;

  const selectedIndex = Math.max(
    0,
    group.tabs.findIndex((t) => isActivePath(pathname, t.href)),
  );

  return (
    <div className="section-tabs-bar shrink-0 border-b border-wos-border-subtle/80 bg-wos-surface/40 backdrop-blur-sm">
      <TabGroup
        selectedIndex={selectedIndex}
        className="mx-auto w-full max-w-[90rem] px-4 sm:px-6 lg:px-8"
      >
        <TabList
          className="section-tabs headless-tab-list headless-tab-list--section flex w-full gap-0.5 overflow-x-auto"
          aria-label={`${group.label} sections`}
        >
          {group.tabs.map((tab) => {
            const active = isActivePath(pathname, tab.href);
            const lock = getNavLock(tab.href);
            const disabling = isLockDisabling(lock);
            return (
              <Tab
                key={tab.href}
                as={Link}
                href={tab.href}
                className={[
                  "section-tab headless-tab",
                  disabling ? "opacity-60" : "",
                  lock?.kind === "soon" ? "pointer-events-none" : "",
                ].join(" ")}
                aria-current={active && !disabling ? "page" : undefined}
                aria-disabled={lock?.kind === "soon" ? true : undefined}
                title={lock?.tooltip ?? tab.description}
              >
                <span className="inline-flex items-center gap-1.5">
                  {tab.label}
                  {lock ? (
                    <span className="rounded-full border border-amber-400/40 bg-amber-500/15 px-1.5 py-0 text-[9px] font-semibold uppercase tracking-wide text-amber-300">
                      {NAV_LOCK_BADGE[lock.kind]}
                    </span>
                  ) : null}
                </span>
              </Tab>
            );
          })}
        </TabList>
      </TabGroup>
    </div>
  );
}
