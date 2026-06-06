export type NavGroupId = "operate" | "games" | "debug" | "assets" | "config";

export type NavTab = {
  href: string;
  label: string;
  description?: string;
};

export type NavGroup = {
  id: NavGroupId;
  label: string;
  description?: string;
  defaultHref: string;
  tabs: NavTab[];
};

export const NAV_GROUPS: NavGroup[] = [
  {
    id: "operate",
    label: "Operate",
    description: "Fleet overview & instance control",
    defaultHref: "/overview",
    tabs: [
      { href: "/overview", label: "Overview" },
      { href: "/instance", label: "Instance" },
      { href: "/player-state", label: "Player state" },
      { href: "/player-stats", label: "Statistics" },
      { href: "/alliance-stats", label: "Alliance stats" },
    ],
  },
  {
    id: "games",
    label: "Games",
    description: "Per-game pages & gift codes",
    defaultHref: "/gift-codes",
    tabs: [
      {
        href: "/gift-codes",
        label: "Gift codes",
        description: "Century Game promo codes per game",
      },
      {
        href: "/dreamscape-memory",
        label: "Dreamscape Memory",
        description: "Item-location guides for the scavenger-hunt event (solo + co-op)",
      },
      {
        href: "/fish-detect",
        label: "Fish detect",
        description: "Fishing Tournament model debugger",
      },
    ],
  },
  {
    id: "debug",
    label: "Debug",
    description: "Approvals, queue & tooling",
    defaultHref: "/approvals",
    tabs: [
      {
        href: "/approvals",
        label: "Click approvals",
        description: "Live approve / reject",
      },
      {
        href: "/overlay-test",
        label: "Overlay test",
        description: "Rule match debugger",
      },
      {
        href: "/map-stitch",
        label: "Map stitch",
        description: "Capture + stitch the world map over scrcpy",
      },
      {
        href: "/notify-monitor",
        label: "Notify monitor",
        description: "Android notification events per player",
      },
      {
        href: "/queue",
        label: "Queue",
        description: "Fleet task queue",
      },
      { href: "/routes", label: "Routes" },
      { href: "/optimizer", label: "Optimizer" },
    ],
  },
  {
    id: "assets",
    label: "Assets",
    description: "Wiki, labeling & reference",
    defaultHref: "/labeling",
    tabs: [
      { href: "/labeling", label: "Labeling" },
      { href: "/gallery", label: "Gallery" },
      { href: "/edit-dsl", label: "DSL editor" },
      { href: "/wiki", label: "Wiki reference" },
    ],
  },
  {
    id: "config",
    label: "Config",
    description: "ADB, modules & balance",
    defaultHref: "/adb",
    tabs: [
      { href: "/adb", label: "ADB" },
      { href: "/modules", label: "Modules" },
      { href: "/balance", label: "Balance" },
      { href: "/license", label: "License" },
    ],
  },
];

export function allNavTabs(): NavTab[] {
  return NAV_GROUPS.flatMap((g) => g.tabs);
}

export function groupForPath(pathname: string): NavGroup | undefined {
  return NAV_GROUPS.find((g) =>
    g.tabs.some(
      (t) => pathname === t.href || pathname.startsWith(`${t.href}/`),
    ),
  );
}

export function labelForHref(href: string): string {
  const tab = allNavTabs().find((t) => t.href === href);
  return tab?.label ?? href;
}
