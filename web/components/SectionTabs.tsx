"use client";

import { Tab, TabGroup, TabList } from "@headlessui/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_GROUPS, type NavGroupId } from "@/lib/nav-groups";

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
            return (
              <Tab
                key={tab.href}
                as={Link}
                href={tab.href}
                className="section-tab headless-tab"
                aria-current={active ? "page" : undefined}
                title={tab.description}
              >
                {tab.label}
              </Tab>
            );
          })}
        </TabList>
      </TabGroup>
    </div>
  );
}
